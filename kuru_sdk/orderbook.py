from math import ceil, floor, log10
from web3 import Web3
from typing import Optional, List, Tuple, Dict, Any
import json
import os

from kuru_sdk.utils import get_error_message

from .types import MarketParams, OrderCreatedEvent, TxOptions, L2Book, FormattedL2Book, OrderPriceSize, VaultParams


class Orderbook:
    def __init__(
        self,
        web3: Web3,
        contract_address: str,
        private_key: Optional[str] = None
    ):
        """
        Orderbook class
        Functions to interact with the Orderbook contract
        
        Initialize the Orderbook SDK
        Args:
            web3: Web3 instance
            contract_address: Address of the deployed Orderbook contract
            private_key: Private key for signing transactions (optional)
        """
        self.web3 = web3
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.private_key = private_key
        
        # Load ABI from JSON file
        with open(os.path.join(os.path.dirname(__file__), 'abi/orderbook.json'), 'r') as f:
            contract_abi = json.load(f)
        
        self.contract = self.web3.eth.contract(
            address=self.contract_address,
            abi=contract_abi
        )
        
        self.market_params = self._fetch_market_params()

    def _fetch_market_params(self) -> MarketParams:
        params = self.contract.functions.getMarketParams().call()
        return MarketParams(
            price_precision=params[0],
            size_precision=params[1],
            base_asset=params[2],
            base_asset_decimals=params[3],
            quote_asset=params[4],
            quote_asset_decimals=params[5],
            tick_size=params[6],
            min_size=params[7],
            max_size=params[8],
            taker_fee_bps=params[9],
            maker_fee_bps=params[10]
        )

    def normalize_with_precision(self, price: str, size: str) -> Tuple[int, int]:
        """Normalize price and size with contract precision"""
        try:
            price_normalized = float(price) * float(str(self.market_params.price_precision))
            size_normalized = float(size) * float(str(self.market_params.size_precision))
            return (int(price_normalized), int(size_normalized))
        except (ValueError, TypeError) as e:
            raise "Error normalizing values: " + str(e)

    async def _prepare_transaction(
        self, 
        function_name: str, 
        args: List[Any],
        tx_options: TxOptions,
        value: int = 0
    ) -> Dict:
        """Helper method to prepare transaction parameters"""
        func = getattr(self.contract.functions, function_name)(*args)

        tx = {
            'to': self.contract_address,
            'value': value,
            'data': func._encode_transaction_data(),
            'from': self.web3.eth.account.from_key(self.private_key).address,
            'type': '0x2',  # EIP-1559 transaction type
            'chainId': self.web3.eth.chain_id
        }

        # Get base fee from latest block
        base_fee = self.web3.eth.get_block('latest')['baseFeePerGas']

        # Set maxPriorityFeePerGas (tip) and maxFeePerGas
        if tx_options.gas_price is not None:
            tx['maxFeePerGas'] = tx_options.gas_price
            tx['maxPriorityFeePerGas'] = tx_options.max_priority_fee_per_gas or min(tx_options.gas_price - base_fee, tx_options.gas_price // 4)
        else:
            # Default to 2x current base fee for maxFeePerGas
            tx['maxFeePerGas'] = base_fee * 2
            # Default priority fee (can be adjusted based on network conditions)
            tx['maxPriorityFeePerGas'] = tx_options.max_priority_fee_per_gas or self.web3.eth.max_priority_fee

        # Ensure maxPriorityFeePerGas is not greater than maxFeePerGas
        if tx['maxPriorityFeePerGas'] > tx['maxFeePerGas']:
            tx['maxPriorityFeePerGas'] = tx['maxFeePerGas']
        
        if tx_options.gas_limit is None:
            estimated_gas = self.web3.eth.estimate_gas(tx)
            tx['gas'] = estimated_gas
        else:
            tx['gas'] = tx_options.gas_limit

        if tx_options.nonce is not None:
            tx['nonce'] = tx_options.nonce
        else:
            tx['nonce'] = self.web3.eth.get_transaction_count(tx['from'])
        return tx

    async def _execute_transaction(self, tx: Dict) -> Tuple[str, Optional[int]]:
        """Execute prepared transaction and return hash and order ID if applicable"""
        try:
            if self.private_key:
                signed_tx = self.web3.eth.account.sign_transaction(tx, self.private_key)
                tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)
            else:
                tx_hash = self.web3.eth.send_transaction(tx)
            
            return tx_hash.hex()
        except Exception as e:
            raise "Error executing transaction: " + str(e)

    async def prepare_buy_order(
        self,
        price: str,
        size: str,
        post_only: bool,
        tick_normalization: Optional[str] = None,
        tx_options: TxOptions = TxOptions()
    ) -> Dict:

        price_normalized, size_normalized = self.normalize_with_precision(price, size)

        price_mod = price_normalized % self.market_params.tick_size

        if tick_normalization == "round_up":
            # round up to the nearest tick
            if price_mod > 0:
                price_normalized = price_normalized + (self.market_params.tick_size - price_mod)
        elif tick_normalization == "round_down":
            # round down to the nearest tick
            price_normalized = price_normalized - price_mod
        else:
            # no normalization then clip the price if it's not divisible by the tick size 
            price_normalized = price_normalized - price_mod

        return await self._prepare_transaction(
            "addBuyOrder",
            [price_normalized, size_normalized, post_only],
            tx_options
        )

    async def add_buy_order(
        self,
        price: str,
        size: str,
        post_only: bool,
        tick_normalization: Optional[str] = None,
        tx_options: TxOptions = TxOptions()
    ) -> str:
        try:
            tx = await self.prepare_buy_order(price, size, post_only, tick_normalization, tx_options)
            return await self._execute_transaction(tx)
        except Exception as e:
            raise Exception(f"Error adding buy order: {get_error_message(str(e))}")

    async def prepare_sell_order(
        self,
        price: str,
        size: str,
        post_only: bool,
        tick_normalization: Optional[str] = None,
        tx_options: TxOptions = TxOptions()
    ) -> Dict:
        if tick_normalization == "round_up":
            price = self.market_params.tick_size * ceil(float(price) / self.market_params.tick_size)
        elif tick_normalization == "round_down":
            price = self.market_params.tick_size * floor(float(price) / self.market_params.tick_size)
        else:
            price = price

        price_normalized, size_normalized = self.normalize_with_precision(price, size)
        return await self._prepare_transaction(
            "addSellOrder",
            [price_normalized, size_normalized, post_only],
            tx_options
        )

    async def add_sell_order(
        self,
        price: str,
        size: str,
        post_only: bool,
        tick_normalization: Optional[str] = None,
        tx_options: TxOptions = TxOptions()
    ) -> str:
        try:
            tx = await self.prepare_sell_order(price, size, post_only, tick_normalization, tx_options)
            return await self._execute_transaction(tx)
        except Exception as e:
            raise Exception(f"Error adding sell order: {get_error_message(str(e))}")

    async def prepare_batch_cancel_orders(
        self,
        order_ids: List[int],
        tx_options: TxOptions = TxOptions()
    ) -> Dict:
        return await self._prepare_transaction(
            "batchCancelOrders",
            [order_ids],
            tx_options
        )

    async def batch_cancel_orders(
        self,
        order_ids: List[int],
        tx_options: TxOptions = TxOptions()
    ) -> str:
        try:
            tx = await self.prepare_batch_cancel_orders(order_ids, tx_options)
            tx_hash = await self._execute_transaction(tx)
            return tx_hash
        except Exception as e:
            raise Exception(f"Error batch canceling orders: {get_error_message(str(e))}")
    
    async def prepare_market_buy(
        self,
        size: str,
        min_amount_out: str,
        is_margin: bool,
        fill_or_kill: bool,
        tx_options: TxOptions = TxOptions()
    ) -> Dict:
        size_normalized = float(size) * float(str(self.market_params.price_precision))
        min_amount_normalized = float(min_amount_out) * float(str(self.market_params.size_precision))
        
        # Calculate value if quote asset is zero address and not margin
        value = 0
        if not is_margin and self.market_params.quote_asset == "0x0000000000000000000000000000000000000000":
            value = int(float(size) * float(str(10 ** self.market_params.quote_asset_decimals)))
        
        return await self._prepare_transaction(
            "placeAndExecuteMarketBuy",
            [int(size_normalized), int(min_amount_normalized), is_margin, fill_or_kill],
            tx_options,
            value=value
        )

    async def market_buy(
        self,
        size: str,
        min_amount_out: str,
        is_margin: bool,
        fill_or_kill: bool,
        tx_options: TxOptions = TxOptions()
    ) -> str:
        tx = await self.prepare_market_buy(
            size, min_amount_out, is_margin, fill_or_kill, 
            tx_options
        )
        tx_hash = await self._execute_transaction(tx)
        return tx_hash

    async def prepare_market_sell(
        self,
        size: str,
        min_amount_out: str,
        is_margin: bool,
        fill_or_kill: bool,
        tx_options: TxOptions = TxOptions()
    ) -> Dict:
        size_normalized = float(size) * float(str(self.market_params.size_precision))
        min_amount_normalized = float(min_amount_out) * float(str(self.market_params.size_precision))
        
        # Calculate value if base asset is zero address and not margin
        value = 0
        if not is_margin and self.market_params.base_asset == "0x0000000000000000000000000000000000000000":
            value = int(float(size) * float(str(10 ** self.market_params.base_asset_decimals)))
        
        return await self._prepare_transaction(
            "placeAndExecuteMarketSell",
            [int(size_normalized), int(min_amount_normalized), is_margin, fill_or_kill],
            tx_options,
            value=value
        )

    async def market_sell(
        self,
        size: str,
        min_amount_out: str,
        is_margin: bool,
        fill_or_kill: bool,
        tx_options: TxOptions = TxOptions()
    ) -> str:
        try:
            tx = await self.prepare_market_sell(
                size, min_amount_out, is_margin, fill_or_kill,
                tx_options
            )
            tx_hash = await self._execute_transaction(tx)
            return tx_hash
        except Exception as e:
            raise Exception(f"Error market selling: {get_error_message(str(e))}")

    async def prepare_batch_orders(
        self,
        buy_prices: List[str],
        buy_sizes: List[str],
        sell_prices: List[str],
        sell_sizes: List[str],
        order_ids_to_cancel: List[str],
        post_only: bool,
        tx_options: TxOptions = TxOptions()
    ) -> Dict:
        normalized_buy_prices = []
        normalized_buy_sizes = []
        normalized_sell_prices = []
        normalized_sell_sizes = []
        
        for price, size in zip(buy_prices, buy_sizes):
            price_norm, size_norm = self.normalize_with_precision(price, size)
            normalized_buy_prices.append(price_norm)
            normalized_buy_sizes.append(size_norm)
            
        for price, size in zip(sell_prices, sell_sizes):
            price_norm, size_norm = self.normalize_with_precision(price, size)
            normalized_sell_prices.append(price_norm)
            normalized_sell_sizes.append(size_norm)

        order_ids = [int(order_id) for order_id in order_ids_to_cancel]
        
        return await self._prepare_transaction(
            "batchUpdate",
            [
                normalized_buy_prices,
                normalized_buy_sizes,
                normalized_sell_prices,
                normalized_sell_sizes,
                order_ids,
                post_only
            ],
            tx_options
        )

    async def batch_orders(
        self,
        buy_prices: Optional[List[str]] = [],
        buy_sizes: Optional[List[str]] = [],
        sell_prices: Optional[List[str]] = [],
        sell_sizes: Optional[List[str]] = [],
        order_ids_to_cancel: Optional[List[str]] = [],
        post_only: Optional[bool] = False,
        tx_options: TxOptions = TxOptions()
    ) -> str:
        try:
            tx = await self.prepare_batch_orders(
                buy_prices, buy_sizes, sell_prices, sell_sizes,
                order_ids_to_cancel, post_only, tx_options
            )
            tx_hash = await self._execute_transaction(tx)
            return tx_hash
        except Exception as e:
            raise Exception(f"Error batching orders: {get_error_message(str(e))}")

    async def get_vault_params(self) -> Dict[str, Any]:
        """Fetch vault parameters from the contract"""
        try:
            # Call the contract function to get vault parameters
            vault_params = await self.contract.functions.getVaultParams().call()

            return {
                'kuru_amm_vault': vault_params[0],
                'vault_best_bid': int(vault_params[1]),
                'bid_partially_filled_size': int(vault_params[2]),
                'vault_best_ask': int(vault_params[3]),
                'ask_partially_filled_size': int(vault_params[4]),
                'vault_bid_order_size': int(vault_params[5]),
                'vault_ask_order_size': int(vault_params[6]),
                'spread': int(vault_params[7])
            }
        except Exception as e:
            raise "Error fetching vault parameters: " + str(e)
        
    async def get_vault_params_from_contract(self) -> VaultParams:
        """Fetch vault parameters from the contract"""
        vault_params = self.contract.functions.getVaultParams().call()
        return VaultParams(
            kuru_amm_vault=vault_params[0],
            vault_best_bid=vault_params[1],
            bid_partially_filled_size=vault_params[2],
            vault_best_ask=vault_params[3],
            ask_partially_filled_size=vault_params[4],
            vault_bid_order_size=vault_params[5],
            vault_ask_order_size=vault_params[6],
            spread=vault_params[7]
        )
    
    async def fetch_orderbook(self) -> L2Book:
        """
        Fetch the current state of the orderbook including both regular orders and AMM prices.
        
        Returns:
            L2Book: Current state of the orderbook containing buy and sell orders
        """
        # Get raw L2 book data from contract
        l2_book_data = self.contract.functions.getL2Book().call()
        
        # Parse raw L2 data into price/size orders
        buy_orders = []
        sell_orders = []
        current_orders = buy_orders
        
        # First 32 bytes are block number
        block_num = int.from_bytes(l2_book_data[:32], 'big')
        offset = 32
        
        while offset + 32 <= len(l2_book_data):
            price_bytes = l2_book_data[offset:offset + 32]
            price = int.from_bytes(price_bytes, 'big')
            
            # Zero price indicates switch from buy to sell orders
            if price == 0:
                current_orders = sell_orders
                offset += 32
                continue
                
            if offset + 64 > len(l2_book_data):
                break
                
            size_bytes = l2_book_data[offset + 32:offset + 64]
            size = int.from_bytes(size_bytes, 'big')
            
            # Normalize price and size using market parameters
            price_float = float(price) / float(self.market_params.price_precision)
            size_float = float(size) / float(self.market_params.size_precision)
            
            current_orders.append(OrderPriceSize(
                price=price_float,
                size=size_float
            ))
            
            offset += 64
        
        # Reverse sell orders to show highest price first
        sell_orders.reverse()
        
        # Get AMM prices
        amm_prices = await self._get_amm_prices()
        total_decimals = log10(self.market_params.price_precision / self.market_params.tick_size)
        
        [amm_buy_orders, amm_sell_orders] = amm_prices


        # round buy_orders down to total_decimals
        # round sell_orders up to total_decimals
        buy_orders = [OrderPriceSize(price=floor(order.price * 10**total_decimals) / 10**total_decimals, size=order.size) for order in buy_orders]
        sell_orders = [OrderPriceSize(price=ceil(order.price * 10**total_decimals) / 10**total_decimals, size=order.size) for order in sell_orders]
        
        vault_params = await self.get_vault_params_from_contract()

        return L2Book(
            block_num=block_num,
            buy_orders=buy_orders,
            sell_orders=sell_orders,
            amm_buy_orders=amm_buy_orders, 
            amm_sell_orders=amm_sell_orders,
            vault_params=vault_params
        )
    
    async def get_l2_book(self):
        """
        Get the current state of the orderbook including both regular orders and AMM prices.
        
        Returns:
            L2Book: Current state of the orderbook containing buy and sell orders
        """
        orderbook = await self.fetch_orderbook()

        combined_buys = {}
        combined_sells = {}

        # Process regular orders
        for order in orderbook.buy_orders:
            combined_buys[order.price] = order.size
        for order in orderbook.sell_orders:
            combined_sells[order.price] = order.size

        # # Add AMM orders, combining sizes for matching prices
        for order in orderbook.amm_buy_orders:
            combined_buys[order.price] = combined_buys.get(order.price, 0) + order.size
        for order in orderbook.amm_sell_orders:
            combined_sells[order.price] = combined_sells.get(order.price, 0) + order.size

        # Convert to sorted lists (sells in descending order)
        sorted_buys = sorted(combined_buys.items(), key=lambda x: x[0], reverse=True)[:10]  # Top 10 bids
        sorted_sells = sorted(combined_sells.items(), key=lambda x: x[0], reverse=True)[-10:]  # Last 10 asks

        # Convert to OrderPriceSize to [Price, Size]
        sorted_buys = [[price, size] for price, size in sorted_buys]
        sorted_sells = [[price, size] for price, size in sorted_sells]
        
        return [sorted_sells, sorted_buys]

    
    def get_order_id_from_receipt(self, receipt) -> int | None:
        """
        Get the order id by decoding the OrderCreated event from the transaction receipt logs.

        Args:
            orderbook: The Orderbook instance containing the contract object.
            receipt: The transaction receipt obtained from web3.eth.wait_for_transaction_receipt.

        Returns:
                The order ID if an OrderCreated event is found, otherwise None.
            """
        contract = self.contract

        try:
            # Process the receipt to find 'OrderCreated' events
            decoded_logs = contract.events.OrderCreated().process_receipt(receipt)

            if decoded_logs:
                # Assuming the first OrderCreated event contains the relevant orderId
                order_id = decoded_logs[0]['args']['orderId']
                return order_id
            else:
                print("No OrderCreated event found in the transaction receipt.")
                return None
        except Exception as e:
            # Handle potential errors during decoding (e.g., event not found in ABI)
            print(f"Error decoding logs from receipt: {e}")
            return None


    def decode_logs(self, receipt) -> list[OrderCreatedEvent]:
        tx_logs = receipt.get('logs')
        order_created_events = []
        for log in tx_logs:
            try:
                order_created_event = self.contract.events.OrderCreated().process_log(log)
                print(f"Order created event: {order_created_event}")
                if order_created_event:
                    order_created_event = OrderCreatedEvent(
                        order_id=order_created_event['args']['orderId'],
                    price=order_created_event['args']['price'],
                    size=order_created_event['args']['size'],
                    is_buy=order_created_event['args']['isBuy']
                    )
                order_created_events.append(order_created_event)
            except Exception as e:
                print(f"Error decoding logs for order created event: {e}")
                continue

        return order_created_events


    async def _get_amm_prices(self) -> Tuple[List[OrderPriceSize], List[OrderPriceSize]]:
        """
        Get the AMM vault prices for both buy and sell sides.
        
        Returns:
            Tuple[List[OrderPriceSize], List[OrderPriceSize]]: Buy and sell orders from AMM
        """
        TOTAL_PRICE_POINTS = 300
        bids = []
        asks = []
        
        # Get vault parameters
        vault_params = self.contract.functions.getVaultParams().call()
        
        vault_best_bid = vault_params[1]
        vault_best_ask = vault_params[3]
        vault_bid_order_size = vault_params[5]
        vault_ask_order_size = vault_params[6]
        kuru_amm_vault = vault_params[0]
        bid_partially_filled_size = vault_params[2]
        ask_partially_filled_size = vault_params[4]
        
        # Convert sizes to float using precision
        vault_bid_size_float = float(vault_bid_order_size) / float(self.market_params.size_precision)
        vault_ask_size_float = float(vault_ask_order_size) / float(self.market_params.size_precision)
        
        # Calculate first order sizes accounting for partial fills
        first_bid_size = float(vault_bid_order_size - bid_partially_filled_size) / float(self.market_params.size_precision)
        first_ask_size = float(vault_ask_order_size - ask_partially_filled_size) / float(self.market_params.size_precision)
        
        # Skip if no AMM vault or no bid size
        if vault_bid_order_size == 0 or kuru_amm_vault == "0x0000000000000000000000000000000000000000":
            return bids, asks
            
        # Generate bid prices
        for i in range(TOTAL_PRICE_POINTS):
            if vault_best_bid == 0:
                break
                
            bids.append(OrderPriceSize(
                price=self._wei_to_eth(vault_best_bid),
                size=first_bid_size if i == 0 else vault_bid_size_float
            ))
            
            # Update price and size for next level
            vault_best_bid = (vault_best_bid * 1000) // 1003
            vault_bid_order_size = (vault_bid_order_size * 2003) // 2000
            vault_bid_size_float = float(vault_bid_order_size) / float(self.market_params.size_precision)
        
        # Generate ask prices
        for i in range(TOTAL_PRICE_POINTS):
            if vault_best_ask >= (2**256 - 1):  # Check for uint256 overflow
                break
                
            asks.append(OrderPriceSize(
                price=self._wei_to_eth(vault_best_ask),
                size=first_ask_size if i == 0 else vault_ask_size_float
            ))
            
            # Update price and size for next level
            vault_best_ask = (vault_best_ask * 1003) // 1000
            vault_ask_order_size = (vault_ask_order_size * 2000) // 2003
            vault_ask_size_float = float(vault_ask_order_size) / float(self.market_params.size_precision)
        
        return bids, asks
    
    def _get_amm_prices_for_vault(self, vault_params: VaultParams) -> Tuple[List[OrderPriceSize], List[OrderPriceSize]]:
        """
        Get the AMM vault prices for both buy and sell sides.
        
        Returns:
            Tuple[List[OrderPriceSize], List[OrderPriceSize]]: Buy and sell orders from AMM
        """
        TOTAL_PRICE_POINTS = 300
        bids = []
        asks = []
        
        
        vault_best_bid = vault_params.vault_best_bid
        vault_best_ask = vault_params.vault_best_ask
        vault_bid_order_size = vault_params.vault_bid_order_size
        vault_ask_order_size = vault_params.vault_ask_order_size
        kuru_amm_vault = vault_params.kuru_amm_vault
        bid_partially_filled_size = vault_params.bid_partially_filled_size
        ask_partially_filled_size = vault_params.ask_partially_filled_size
        
        # Convert sizes to float using precision
        vault_bid_size_float = float(vault_bid_order_size) / float(self.market_params.size_precision)
        vault_ask_size_float = float(vault_ask_order_size) / float(self.market_params.size_precision)
        
        # Calculate first order sizes accounting for partial fills
        first_bid_size = float(vault_bid_order_size - bid_partially_filled_size) / float(self.market_params.size_precision)
        first_ask_size = float(vault_ask_order_size - ask_partially_filled_size) / float(self.market_params.size_precision)
        
        # Skip if no AMM vault or no bid size
        if vault_bid_order_size == 0 or kuru_amm_vault == "0x0000000000000000000000000000000000000000":
            return bids, asks
            
        # Generate bid prices
        for i in range(TOTAL_PRICE_POINTS):
            if vault_best_bid == 0:
                break
                
            bids.append(OrderPriceSize(
                price=self._wei_to_eth(vault_best_bid),
                size=first_bid_size if i == 0 else vault_bid_size_float
            ))
            
            # Update price and size for next level
            vault_best_bid = (vault_best_bid * 1000) // 1003
            vault_bid_order_size = (vault_bid_order_size * 2003) // 2000
            vault_bid_size_float = float(vault_bid_order_size) / float(self.market_params.size_precision)
        
        # Generate ask prices
        for i in range(TOTAL_PRICE_POINTS):
            if vault_best_ask >= (2**256 - 1):  # Check for uint256 overflow
                break
                
            asks.append(OrderPriceSize(
                price=self._wei_to_eth(vault_best_ask),
                size=first_ask_size if i == 0 else vault_ask_size_float
            ))
            
            # Update price and size for next level
            vault_best_ask = (vault_best_ask * 1003) // 1000
            vault_ask_order_size = (vault_ask_order_size * 2000) // 2003
            vault_ask_size_float = float(vault_ask_order_size) / float(self.market_params.size_precision)
        
        return bids, asks

    def _wei_to_eth(self, wei_value: int) -> float:
        """Convert Wei to ETH"""
        return float(wei_value) / 1e18
    
    def reconcile_orderbook(self, orderbook: L2Book, event: str, payload: Dict) -> L2Book:
        """Reconcile the orderbook to ensure it is updated with new orders / trades"""

        if event == "OrderCreated":
            orderbook = self._reconcile_orderbook_for_order_created(orderbook, payload)
        elif event == "OrderCancelled":
            orderbook = self._reconcile_orderbook_for_order_cancelled(orderbook, payload)
        elif event == "Trade":
            orderbook = self._reconcile_orderbook_for_trade(orderbook, payload)

        print(f"Reconciled orderbook: {orderbook.vault_params}")
        return orderbook
    


    def _reconcile_orderbook_for_order_created(self, orderbook: L2Book, payload: Dict) -> L2Book:
        """Reconcile the orderbook for an order created event"""
        total_decimals = log10(self.market_params.price_precision / self.market_params.tick_size)
        is_buy = payload['isBuy']
        size = int(payload['size']) / self.market_params.size_precision

        buy_orders = orderbook.buy_orders
        sell_orders = orderbook.sell_orders
        block_num = payload['blockNumber']

        if is_buy:
            price = floor((int(payload['price']) / self.market_params.price_precision) * 10**total_decimals) / 10**total_decimals 
            print(f"Price: {price}")
            print("%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%")
            # find the corresponding price in the buy_orders list
            for order in buy_orders:
                if order.price == price:
                    order.size += size
                    break
            else:
                buy_orders.append(OrderPriceSize(price=price, size=size))
        else:
            price = ceil((int(payload['price']) / self.market_params.price_precision) * 10**total_decimals) / 10**total_decimals 
            # find the corresponding price in the sell_orders list
            for order in sell_orders:
                if order.price == price:
                    order.size += size
                    break
            else:
                sell_orders.append(OrderPriceSize(price=price, size=size))

        return L2Book(
            block_num=block_num,
            buy_orders=buy_orders,
            sell_orders=sell_orders,
            amm_buy_orders=orderbook.amm_buy_orders,
            amm_sell_orders=orderbook.amm_sell_orders,
            vault_params=orderbook.vault_params
        )
    
    def _reconcile_orderbook_for_order_cancelled(self, orderbook: L2Book, payload: Dict) -> L2Book:
        """Reconcile the orderbook for an order cancelled event"""
        
        total_decimals = log10(self.market_params.price_precision / self.market_params.tick_size)

        orders_cancelled = payload['canceledOrdersData']

        block_num = orders_cancelled[0]['blockNumber']
        buy_orders = orderbook.buy_orders
        sell_orders = orderbook.sell_orders

        for order in orders_cancelled:
            if order['isBuy']:
                size = int(order['size']) / self.market_params.size_precision
                price = floor((int(order['price']) / 10**18) * 10**total_decimals) / 10**total_decimals
                # find the corresponding price in the buy_orders list
                for order in buy_orders:
                    if order.price == price:
                        order.size -= size
                        break
                else:
                    buy_orders.append(OrderPriceSize(price=price, size=size))
            else:
                size = order['size'] / self.market_params.size_precision
                price = ceil((payload['price'] / 10**18) * 10**total_decimals) / 10**total_decimals
                # find the corresponding price in the sell_orders list
                for order in sell_orders:
                    if order.price == price:
                        order.size -= size
                        break

        return L2Book(
            block_num=block_num,
            buy_orders=buy_orders,
            sell_orders=sell_orders,
            amm_buy_orders=orderbook.amm_buy_orders,
            amm_sell_orders=orderbook.amm_sell_orders,
            vault_params=orderbook.vault_params
        )
    
    def _reconcile_orderbook_for_trade(self, orderbook: L2Book, payload: Dict) -> L2Book:
        block_num = payload['blockNumber']
        new_orderbook = L2Book(
            block_num=block_num,
            buy_orders=orderbook.buy_orders,
            sell_orders=orderbook.sell_orders,
            amm_buy_orders=orderbook.amm_buy_orders,
            amm_sell_orders=orderbook.amm_sell_orders,
            vault_params=orderbook.vault_params
        )

        print(f"New orderbook: {new_orderbook.vault_params}")

        total_decimals = log10(self.market_params.price_precision / self.market_params.tick_size)

        order_id = payload['orderId']
            
        print(f"Order ID: {order_id}")

        if order_id == 0:
            # amm trade
            print(f"Handling AMM trade")
            new_orderbook = self._handle_amm_trade(new_orderbook, payload)
        else:
            # regular trade
            print(f"Handling regular trade")
            new_orderbook = self._handle_regular_trade(new_orderbook, payload)

        print(f"New orderbook: {new_orderbook.vault_params}")

        return new_orderbook

    def _handle_amm_trade(self, orderbook: L2Book, payload: Dict) -> L2Book:
        """Handle an AMM trade event"""
        is_buy = payload['isBuy']

        SPREAD = orderbook.vault_params.spread / 10
        updated_size = int(payload['updatedSize'])

        if is_buy:

            if updated_size == 0:
                orderbook.vault_params.vault_best_ask = (orderbook.vault_params.vault_best_ask * (1000 + SPREAD)) // 1000
                orderbook.vault_params.vault_best_bid = (orderbook.vault_params.vault_best_bid * 1000) // (1000 + SPREAD)
                orderbook.vault_params.vault_ask_order_size = (orderbook.vault_params.vault_ask_order_size * 2000) // (2000 + SPREAD)
                orderbook.vault_params.vault_bid_order_size = (orderbook.vault_params.vault_ask_order_size * (2000 + SPREAD)) // 2000
                orderbook.vault_params.ask_partially_filled_size = 0
                orderbook.vault_params.bid_partially_filled_size = (orderbook.vault_params.bid_partially_filled_size * 1000) // (1000 + SPREAD)

            else:
                orderbook.vault_params.ask_partially_filled_size = orderbook.vault_params.vault_ask_order_size - updated_size
            
        else:
            if updated_size == 0:
                orderbook.vault_params.vault_best_bid = (orderbook.vault_params.vault_best_bid * 1000) // (1000 + SPREAD)
                orderbook.vault_params.vault_best_ask = (orderbook.vault_params.vault_best_ask * (1000 + SPREAD)) // 1000
                orderbook.vault_params.vault_bid_order_size = (orderbook.vault_params.vault_bid_order_size * (2000 + SPREAD)) // 2000
                orderbook.vault_params.vault_ask_order_size = (orderbook.vault_params.vault_bid_order_size * 2000) // (2000 + SPREAD)
                orderbook.vault_params.bid_partially_filled_size = 0
                
            else:
                orderbook.vault_params.bid_partially_filled_size = orderbook.vault_params.vault_bid_order_size - updated_size
            
        print(f"Acquired vault params: {orderbook.vault_params}")
        amm_prices = self._get_amm_prices_for_vault(orderbook.vault_params)
        
        print(f"Acquired AMM prices")
        orderbook.amm_buy_orders = amm_prices[0]
        orderbook.amm_sell_orders = amm_prices[1]

        return orderbook


    def _handle_regular_trade(self, orderbook: L2Book, payload: Dict) -> L2Book:
        """Handle a regular trade event"""
        filled_size = payload['filledSize'] / self.market_params.size_precision  # Fixed conversion

        raw_trade_price = payload['price']  # Price should already be in correct format from contract
        formatted_price = self.format_price(raw_trade_price, payload['isBuy'])

        side_to_update = orderbook.buy_orders if payload['isBuy'] else orderbook.sell_orders

        # Find the order at the given price level
        for i, order in enumerate(side_to_update):
            if order.price == formatted_price:
                # Update the size
                new_size = order.size - filled_size
                if new_size <= 0:
                    # Remove the price level if size is zero or negative
                    side_to_update.pop(i)
                else:
                    # Update the size if there's remaining quantity
                    side_to_update[i] = OrderPriceSize(price=formatted_price, size=new_size)
                break

        # Update the appropriate side in the orderbook
        if payload['isBuy']:
            orderbook.buy_orders = side_to_update
        else:
            orderbook.sell_orders = side_to_update

        return orderbook

    def format_price(self, price: int, is_buy: bool) -> float:
        """Format a price to the correct precision"""
        total_decimals = log10(self.market_params.price_precision / self.market_params.tick_size)
        if is_buy:
            return floor(price / 10**18 * 10**total_decimals) / 10**total_decimals
        else:
            return ceil(price / 10**18 * 10**total_decimals) / 10**total_decimals

    def get_formatted_orderbook(self) -> FormattedL2Book:
        """
        Combines regular and AMM orders into a single formatted orderbook.
        
        Returns:
            FormattedL2Book: Combined orderbook with merged regular and AMM orders
        """
        # Combine regular and AMM orders
        combined_buys = {}
        combined_sells = {}

        # Process regular orders
        for order in self.buy_orders:
            combined_buys[order.price] = order.size
        for order in self.sell_orders:
            combined_sells[order.price] = order.size

        # Add AMM orders, combining sizes for matching prices
        for order in self.amm_buy_orders:
            combined_buys[order.price] = combined_buys.get(order.price, 0) + order.size
        for order in self.amm_sell_orders:
            combined_sells[order.price] = combined_sells.get(order.price, 0) + order.size

        # Convert back to OrderPriceSize objects
        formatted_buys = [
            OrderPriceSize(price=price, size=size)
            for price, size in combined_buys.items()
        ]
        formatted_sells = [
            OrderPriceSize(price=price, size=size)
            for price, size in combined_sells.items()
        ]

        return FormattedL2Book(
            block_num=self.block_num,
            buy_orders=formatted_buys,
            sell_orders=formatted_sells
        )

__all__ = ['Orderbook']
