from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from time import time

# Import for type hints in update_order_on_trade
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.manager.events import TradeEvent


class OrderSide(Enum):
    """Order side: buy or sell"""

    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order type for different operations"""

    LIMIT = "limit"  # Maps to addBuyOrder() / addSellOrder()
    MARKET = (
        "market"  # Maps to placeAndExecuteMarketBuy() / placeAndExecuteMarketSell()
    )
    CANCEL = "cancel"  # Maps to batchUpdate()


class OrderStatus(Enum):
    """Order lifecycle status"""

    ORDER_CREATED = "created"
    ORDER_SENT = "sent"
    ORDER_PLACED = "placed"
    ORDER_PARTIALLY_FILLED = "partially_filled"
    ORDER_FULLY_FILLED = "fully_filled"
    ORDER_CANCELLED = "cancelled"
    ORDER_TIMEOUT = "timeout"


@dataclass
class Order:
    """
    Unified order class that can represent limit, market, and cancel operations.

    Core fields (all orders):
        - cloid: Client order ID (user-provided)
        - order_type: Type of order operation (LIMIT, MARKET, CANCEL)
        - status: Current lifecycle status
        - kuru_order_id: Orderbook order ID (uint40, filled after placement)
        - txhash: Transaction hash (filled after sending to blockchain)
        - timestamp: Creation timestamp

    For LIMIT/MARKET orders:
        - side: Buy or sell direction
        - price: Price as uint32 (LIMIT only)
        - size: Size as uint96
        - post_only: Post-only flag (LIMIT only)

    For MARKET orders only:
        - min_amount_out: Minimum output as uint256
        - is_margin: Margin flag
        - is_fill_or_kill: Fill-or-kill flag

    For CANCEL orders:
        - order_ids_to_cancel: Order IDs (uint40[]) for batch cancellation
    """

    # Core fields (required for all orders)
    cloid: str
    order_type: OrderType
    status: OrderStatus = OrderStatus.ORDER_CREATED
    timestamp: float = field(default_factory=time)

    # Optional: Filled after placement on orderbook
    kuru_order_id: Optional[int] = None

    # Optional: Filled after order is sent to blockchain
    txhash: Optional[str] = None

    # Limit/Market order fields
    side: Optional[OrderSide] = None
    price: Optional[float] = None
    size: Optional[float] = None
    post_only: Optional[bool] = True

    # Market order specific fields
    min_amount_out: Optional[float] = None
    is_margin: Optional[bool] = None
    is_fill_or_kill: Optional[bool] = None

    # Cancel order fields
    order_ids_to_cancel: Optional[list[int]] = None

    # Fill tracking - list of all fill amounts from trade events
    filled_sizes: list[float] = field(default_factory=list)

    def update_status(self, new_status: OrderStatus) -> None:
        """Update the order status"""
        self.status = new_status

    def set_kuru_order_id(self, order_id: int) -> None:
        """
        Set the Kuru orderbook order ID after placement.

        Args:
            order_id: The orderbook order ID (uint40)
        """
        if not (0 <= order_id <= 2**40 - 1):
            raise ValueError(
                f"Order ID must be uint40 (0 to {2**40-1}), got {order_id}"
            )
        self.kuru_order_id = order_id

    def set_txhash(self, txhash: str) -> None:
        """
        Set the transaction hash after sending the order to the blockchain.

        Args:
            txhash: The transaction hash (with or without 0x prefix)
        """
        self.txhash = txhash

    def update_order_on_trade(self, trade_event: "TradeEvent") -> None:
        """Update the order on trade event"""

        # Record this fill in history
        self.filled_sizes.append(trade_event.filled_size)

        if trade_event.updated_size == 0:
            self.size = 0
            self.update_status(OrderStatus.ORDER_FULLY_FILLED)
        else:
            if trade_event.updated_size < self.size:
                self.size = trade_event.updated_size
                self.update_status(OrderStatus.ORDER_PARTIALLY_FILLED)

    @property
    def total_filled_size(self) -> float:
        """Calculate total filled size across all fills."""
        return sum(self.filled_sizes)

    def __repr__(self) -> str:
        """String representation of the order"""
        base = f"Order(cloid={self.cloid}, type={self.order_type.value}, status={self.status.value}"

        if self.kuru_order_id is not None:
            base += f", kuru_id={self.kuru_order_id}"

        if self.order_type in [OrderType.LIMIT, OrderType.MARKET]:
            base += f", side={self.side.value if self.side else None}"
            if self.price is not None:
                base += f", price={self.price}"
            if self.size is not None:
                base += f", size={self.size}"

        if self.order_type == OrderType.CANCEL:
            base += f", cancel_ids={self.order_ids_to_cancel}"

        base += ")"
        return base
