"""EIP-2930 access list computation for Kuru orderbook operations."""

from loguru import logger
from web3 import Web3

__all__ = [
    "build_access_list_for_cancel_and_place",
    "build_access_list_for_cancel_only",
]

# ==================== Constants ====================
# Storage slot indices (from Solidity contracts)
ORDER_SLOT = 50  # mapping(uint40 => Order) s_orders
BUY_PP_SLOT = 51  # mapping(uint256 => PricePoint) s_buyPricePoints
SELL_PP_SLOT = 52  # mapping(uint256 => PricePoint) s_sellPricePoints
MARGIN_BALANCES_SLOT = 1  # mapping(bytes32 => uint256) balances
BUY_TREE_BASE_SLOT = 53  # TreeMath.TreeUint32 s_buyTree (level 0)
SELL_TREE_BASE_SLOT = 57  # TreeMath.TreeUint32 s_sellTree (level 0)
VERIFIED_MARKET_SLOT = 2  # mapping(address => bool) in MarginAccount

# Fixed orderbook slots (these are always accessed)
ORDERBOOK_FIXED_SLOTS = [
    "0x0000000000000000000000000000000000000000000000000000000000000000",  # vaultBestBid
    "0x0000000000000000000000000000000000000000000000000000000000000002",  # vaultBestAsk
    "0x0000000000000000000000000000000000000000000000000000000000000003",  # askPartiallyFilledSize
    "0x0000000000000000000000000000000000000000000000000000000000000004",  # vaultBidOrderSize
    "0x0000000000000000000000000000000000000000000000000000000000000031",  # _trustedForwarder
    "0x0000000000000000000000000000000000000000000000000000000000000035",  # s_buyTree
    "0x0000000000000000000000000000000000000000000000000000000000000039",  # s_sellTree
    "0x000000000000000000000000000000000000000000000000000000000000003d",  # sizePrecision, pricePrecision
    "0x0000000000000000000000000000000000000000000000000000000000000041",  # baseDecimalMultiplier
    "0x0000000000000000000000000000000000000000000000000000000000000043",  # quoteDecimalMultiplier
    "0x0000000000000000000000000000000000000000000000000000000000000044",  # baseAsset
    "0x0000000000000000000000000000000000000000000000000000000000000045",  # quoteAsset
    "0x0000000000000000000000000000000000000000000000000000000000000046",  # maxSize
    "0x0000000000000000000000000000000000000000000000000000000000000049",  # marginAccount
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",  # Implementation slot
]

# Additional slots for cancel + place operations (from Rust reference)
CANCEL_PLACE_EXTRA_SLOTS = [
    "0x33d13c149959174817b07214f6faff3d4c1d39ff89c6e7f82e2df5f04c00a0ec",
    "0x75ae8a0e79dd0a018aa9bf1c84550b9ba9413a6b4d6d81c2029471d98e3db9e5",
    "0x489cb750909c17c66c5ce6e12fc66200b1268a56adfccc87a74969633bf57069",
]

# Fixed margin account slots
MARGIN_FIXED_SLOTS = [
    "0x0000000000000000000000000000000000000000000000000000000000000000",  # protocolPaused
    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",  # Implementation slot
]


# ==================== Storage Slot Computation Helpers ====================


def _keccak_mapping_slot(key: int, slot_index: int) -> str:
    """
    Compute storage slot for Solidity mapping with uint256 key.

    Formula: keccak256(abi.encodePacked(uint256(key), uint256(slot)))

    Args:
        key: The mapping key (uint256)
        slot_index: The base slot index

    Returns:
        Storage slot as hex string with 0x prefix
    """
    key_bytes = key.to_bytes(32, byteorder="big")
    slot_bytes = slot_index.to_bytes(32, byteorder="big")
    packed = key_bytes + slot_bytes
    hash_bytes = Web3.keccak(primitive=packed)
    return "0x" + hash_bytes.hex()


def _keccak_mapping_slot_address(address: str, slot_index: int) -> str:
    """
    Compute storage slot for Solidity mapping with address key.

    Formula: keccak256(abi.encodePacked(address(key), uint256(slot)))

    Args:
        address: The address key (20 bytes)
        slot_index: The base slot index

    Returns:
        Storage slot as hex string with 0x prefix
    """
    # Address is 20 bytes, left-padded to 32 bytes in abi.encodePacked
    addr_bytes = bytes.fromhex(address.replace("0x", "")).rjust(32, b"\x00")
    slot_bytes = slot_index.to_bytes(32, byteorder="big")
    packed = addr_bytes + slot_bytes
    hash_bytes = Web3.keccak(primitive=packed)
    return "0x" + hash_bytes.hex()


def _keccak_mapping_slot_bytes32(key_bytes: bytes, slot_index: int) -> str:
    """
    Compute storage slot for Solidity mapping with bytes32 key.

    Formula: keccak256(abi.encodePacked(bytes32(key), uint256(slot)))

    Args:
        key_bytes: The bytes32 key
        slot_index: The base slot index

    Returns:
        Storage slot as hex string with 0x prefix
    """
    slot_bytes = slot_index.to_bytes(32, byteorder="big")
    packed = key_bytes + slot_bytes
    hash_bytes = Web3.keccak(primitive=packed)
    return "0x" + hash_bytes.hex()


def _account_key(user_address: str, token_address: str) -> bytes:
    """
    Compute margin account key for user-token pair.

    Formula: keccak256(abi.encodePacked(address(user), address(token)))

    Args:
        user_address: User wallet address
        token_address: Token contract address

    Returns:
        32-byte keccak256 hash
    """
    user_bytes = bytes.fromhex(user_address.replace("0x", ""))
    token_bytes = bytes.fromhex(token_address.replace("0x", ""))
    packed = user_bytes + token_bytes
    return Web3.keccak(primitive=packed)


def _format_storage_key(value: int) -> str:
    """
    Format integer as 32-byte hex storage key.

    Args:
        value: Integer value to format

    Returns:
        32-byte hex string with 0x prefix
    """
    return "0x" + value.to_bytes(32, byteorder="big").hex()


# ==================== Order and Price Storage Helpers ====================


def _add_order_slots(storage_keys: set[str], order_id: int) -> None:
    """
    Add storage slots for an order.

    Orders are stored in: mapping(uint40 => Order) s_orders at slot 50
    Each Order struct occupies 2 storage slots.

    Note: order_id is uint40 (5 bytes) but encoded as uint256 in storage key computation.

    Args:
        storage_keys: Set to add storage keys to
        order_id: Order ID (uint40)
    """
    base_slot_hex = _keccak_mapping_slot(order_id, ORDER_SLOT)
    base_slot_int = int(base_slot_hex, 16)

    # Add both slots of the Order struct
    storage_keys.add(base_slot_hex)
    storage_keys.add(_format_storage_key(base_slot_int + 1))


def _add_price_point_slots(
    storage_keys: set[str], prices: set[int], is_buy: bool
) -> None:
    """
    Add storage slots for price points.

    Price points stored in:
    - mapping(uint256 => PricePoint) s_buyPricePoints at slot 51
    - mapping(uint256 => PricePoint) s_sellPricePoints at slot 52

    Args:
        storage_keys: Set to add storage keys to
        prices: Set of price levels (as integers)
        is_buy: True for buy side, False for sell side
    """
    slot_index = BUY_PP_SLOT if is_buy else SELL_PP_SLOT

    for price in prices:
        slot = _keccak_mapping_slot(price, slot_index)
        storage_keys.add(slot)


def _add_tree_slots_for_prices(
    storage_keys: set[str], prices: set[int], is_buy: bool
) -> None:
    """
    Add tree structure slots for prices (levels 0-3).

    Tree structure (TreeMath.TreeUint32):
    - Buy tree base: slot 53 (level0 at 53, level1 at 54, level2 at 55, level3 at 56)
    - Sell tree base: slot 57 (level0 at 57, level1 at 58, level2 at 59, level3 at 60)

    Each level maps tree indices to uint32 bitmaps.
    Tree index shift amounts:
    - level 1: price >> 24
    - level 2: price >> 16
    - level 3: price >> 8

    Args:
        storage_keys: Set to add storage keys to
        prices: Set of price levels (as integers)
        is_buy: True for buy side, False for sell side
    """
    if not prices:
        return

    base_slot = BUY_TREE_BASE_SLOT if is_buy else SELL_TREE_BASE_SLOT

    # Add level 0 (direct bytes32 value, not a mapping)
    storage_keys.add(_format_storage_key(base_slot))

    # Add levels 1, 2, 3 (each is a mapping)
    for price in prices:
        # level 1: shift by 24 bits
        tree_index_l1 = price >> 24
        tree_index_l1_bytes32 = tree_index_l1.to_bytes(32, byteorder="big")
        slot_l1 = _keccak_mapping_slot_bytes32(tree_index_l1_bytes32, base_slot + 1)
        storage_keys.add(slot_l1)

        # level 2: shift by 16 bits
        tree_index_l2 = price >> 16
        tree_index_l2_bytes32 = tree_index_l2.to_bytes(32, byteorder="big")
        slot_l2 = _keccak_mapping_slot_bytes32(tree_index_l2_bytes32, base_slot + 2)
        storage_keys.add(slot_l2)

        # level 3: shift by 8 bits
        tree_index_l3 = price >> 8
        tree_index_l3_bytes32 = tree_index_l3.to_bytes(32, byteorder="big")
        slot_l3 = _keccak_mapping_slot_bytes32(tree_index_l3_bytes32, base_slot + 3)
        storage_keys.add(slot_l3)


def _add_margin_balance_slots(
    storage_keys: set[str],
    user_address: str,
    base_token_address: str,
    quote_token_address: str,
    has_buy_orders: bool,
    has_sell_orders: bool,
) -> None:
    """
    Add margin account balance slots.

    Balances stored in: mapping(bytes32 => uint256) balances at slot 1
    Key: keccak256(abi.encodePacked(user_address, token_address))

    Only add quote token balance if there are buy orders (need quote to buy).
    Only add base token balance if there are sell orders (need base to sell).

    Args:
        storage_keys: Set to add storage keys to
        user_address: User wallet address
        base_token_address: Base token contract address
        quote_token_address: Quote token contract address
        has_buy_orders: Whether there are buy orders
        has_sell_orders: Whether there are sell orders
    """
    if has_buy_orders:
        quote_key = _account_key(user_address, quote_token_address)
        quote_slot = _keccak_mapping_slot_bytes32(quote_key, MARGIN_BALANCES_SLOT)
        storage_keys.add(quote_slot)

    if has_sell_orders:
        base_key = _account_key(user_address, base_token_address)
        base_slot = _keccak_mapping_slot_bytes32(base_key, MARGIN_BALANCES_SLOT)
        storage_keys.add(base_slot)


# ==================== Public API ====================


def build_access_list_for_cancel_and_place(
    user_address: str,
    orderbook_address: str,
    margin_account_address: str,
    base_token_address: str,
    quote_token_address: str,
    orderbook_implementation: str,
    margin_account_implementation: str,
    orders_to_cancel: list[tuple[int, int, bool]],  # (order_id, price, is_buy)
    buy_orders: list[tuple[int, int]],  # (price, size)
    sell_orders: list[tuple[int, int]],  # (price, size)
) -> list[dict]:
    """
    Build EIP-2930 access list for combined cancel and place operations.

    This function computes all storage slots that will be accessed when:
    1. Canceling existing orders
    2. Placing new buy/sell orders

    Args:
        user_address: User's wallet address (EOA)
        orderbook_address: Orderbook contract address
        margin_account_address: Margin account contract address
        base_token_address: Base token address for margin balance
        quote_token_address: Quote token address for margin balance
        orderbook_implementation: Orderbook implementation contract address
        margin_account_implementation: Margin account implementation contract address
        orders_to_cancel: List of (order_id, price, is_buy) for orders being canceled
        buy_orders: List of (price, size) for new buy orders
        sell_orders: List of (price, size) for new sell orders

    Returns:
        List of AccessListEntry dicts with 'address' and 'storageKeys' fields
        Format: [{'address': '0x...', 'storageKeys': ['0x...', ...]}, ...]
    """
    orderbook_keys = set()
    margin_keys = set()

    # Track all unique prices
    buy_prices = set()
    sell_prices = set()

    # Process canceled orders
    if orders_to_cancel:
        for order_id, price, is_buy in orders_to_cancel:
            # Add order storage slots
            _add_order_slots(orderbook_keys, order_id)

            # Track prices for tree and price point updates
            if is_buy:
                buy_prices.add(price)
            else:
                sell_prices.add(price)

        # Add extra slots needed for cancel+place operations
        orderbook_keys.update(CANCEL_PLACE_EXTRA_SLOTS)

    # Process new orders
    for price, size in buy_orders:
        buy_prices.add(price)

    for price, size in sell_orders:
        sell_prices.add(price)

    # Add price point and tree slots
    if buy_prices:
        _add_price_point_slots(orderbook_keys, buy_prices, is_buy=True)
        _add_tree_slots_for_prices(orderbook_keys, buy_prices, is_buy=True)

    if sell_prices:
        _add_price_point_slots(orderbook_keys, sell_prices, is_buy=False)
        _add_tree_slots_for_prices(orderbook_keys, sell_prices, is_buy=False)

    # Add fixed orderbook slots
    orderbook_keys.update(ORDERBOOK_FIXED_SLOTS)

    # Add margin account slots
    margin_keys.update(MARGIN_FIXED_SLOTS)

    # Add verified market slot for this orderbook
    verified_market_slot = _keccak_mapping_slot_address(
        orderbook_address, VERIFIED_MARKET_SLOT
    )
    margin_keys.add(verified_market_slot)

    # Add margin balance slots
    _add_margin_balance_slots(
        margin_keys,
        user_address,
        base_token_address,
        quote_token_address,
        has_buy_orders=len(buy_orders) > 0
        or any(is_buy for _, _, is_buy in orders_to_cancel),
        has_sell_orders=len(sell_orders) > 0
        or any(not is_buy for _, _, is_buy in orders_to_cancel),
    )

    # Build access list
    access_list = [
        {
            "address": Web3.to_checksum_address(orderbook_address),
            "storageKeys": sorted(list(orderbook_keys)),
        },
        {
            "address": Web3.to_checksum_address(margin_account_address),
            "storageKeys": sorted(list(margin_keys)),
        },
        # Implementation contracts (empty storage keys)
        {
            "address": Web3.to_checksum_address(orderbook_implementation),
            "storageKeys": [],
        },
        {
            "address": Web3.to_checksum_address(margin_account_implementation),
            "storageKeys": [],
        },
    ]

    logger.debug(
        f"Built access list: {len(orderbook_keys)} orderbook slots, "
        f"{len(margin_keys)} margin slots, {len(orders_to_cancel)} cancels, "
        f"{len(buy_orders)} buys, {len(sell_orders)} sells"
    )

    return access_list


def build_access_list_for_cancel_only(
    user_address: str,
    orderbook_address: str,
    margin_account_address: str,
    base_token_address: str,
    quote_token_address: str,
    orderbook_implementation: str,
    margin_account_implementation: str,
    orders_to_cancel: list[tuple[int, int, bool]],  # (order_id, price, is_buy)
) -> list[dict]:
    """
    Build EIP-2930 access list for cancel-only operations.

    This is a simplified version for when only canceling orders (no new orders).

    Args:
        user_address: User's wallet address (EOA)
        orderbook_address: Orderbook contract address
        margin_account_address: Margin account contract address
        base_token_address: Base token address for margin balance
        quote_token_address: Quote token address for margin balance
        orderbook_implementation: Orderbook implementation contract address
        margin_account_implementation: Margin account implementation contract address
        orders_to_cancel: List of (order_id, price, is_buy) for orders being canceled

    Returns:
        List of AccessListEntry dicts with 'address' and 'storageKeys' fields
    """
    if not orders_to_cancel:
        logger.warning(
            "build_access_list_for_cancel_only called with no orders to cancel"
        )
        return []

    orderbook_keys = set()
    margin_keys = set()

    buy_prices = set()
    sell_prices = set()

    # Process all orders to cancel
    for order_id, price, is_buy in orders_to_cancel:
        # Add order storage slots
        _add_order_slots(orderbook_keys, order_id)

        # Track prices
        if is_buy:
            buy_prices.add(price)
        else:
            sell_prices.add(price)

    # Add price point and tree slots
    if buy_prices:
        _add_price_point_slots(orderbook_keys, buy_prices, is_buy=True)
        _add_tree_slots_for_prices(orderbook_keys, buy_prices, is_buy=True)

    if sell_prices:
        _add_price_point_slots(orderbook_keys, sell_prices, is_buy=False)
        _add_tree_slots_for_prices(orderbook_keys, sell_prices, is_buy=False)

    # Add margin balance slots (canceling credits back the margin account)
    _add_margin_balance_slots(
        margin_keys,
        user_address,
        base_token_address,
        quote_token_address,
        has_buy_orders=len(buy_prices) > 0,  # Canceling buy orders credits quote token
        has_sell_orders=len(sell_prices)
        > 0,  # Canceling sell orders credits base token
    )

    # Build access list
    access_list = [
        {
            "address": Web3.to_checksum_address(orderbook_address),
            "storageKeys": sorted(list(orderbook_keys)),
        },
        {
            "address": Web3.to_checksum_address(margin_account_address),
            "storageKeys": sorted(list(margin_keys)),
        },
        # Implementation contracts (empty storage keys)
        {
            "address": Web3.to_checksum_address(orderbook_implementation),
            "storageKeys": [],
        },
        {
            "address": Web3.to_checksum_address(margin_account_implementation),
            "storageKeys": [],
        },
    ]

    logger.debug(
        f"Built cancel-only access list: {len(orderbook_keys)} orderbook slots, "
        f"{len(margin_keys)} margin slots, {len(orders_to_cancel)} cancels"
    )

    return access_list
