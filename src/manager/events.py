from dataclasses import dataclass
from typing import Optional


@dataclass
class OrderCreatedEvent:
    """
    Emitted when a new order is created.

    event OrderCreated(uint40 orderId, address owner, uint96 size, uint32 price, bool isBuy)

    Fields:
        - order_id: Unique identifier for the newly created order (uint40)
        - owner: Address of the user who created the order
        - size: Size of the order in the specified precision (uint96)
        - price: Price point of the order in the specified precision (uint32)
        - is_buy: Boolean indicating if the order is a buy (true) or sell (false) order
    """

    order_id: int  # uint40
    owner: str  # address
    size: float
    price: int 
    is_buy: bool

    # Optional: Transaction hash for tracking
    txhash: Optional[str] = None

    # Optional: Log index for tracking
    log_index: Optional[int] = None

    def __repr__(self) -> str:
        side = "buy" if self.is_buy else "sell"
        return (
            f"OrderCreatedEvent(order_id={self.order_id}, owner={self.owner[:10]}..., "
            f"size={self.size}, price={self.price}, side={side}, log_index={self.log_index})"
        )


@dataclass
class OrdersCanceledEvent:
    """
    Emitted when one or more orders are completed or canceled.

    event OrdersCanceled(uint40[] orderId, address owner)

    Fields:
        - order_ids: Array of order identifiers that were completed or canceled (uint40[])
        - owner: Address of the user who owned the orders
    """

    order_ids: list[int]  # uint40[]
    owner: str  # address

    # Optional: Transaction hash for tracking
    txhash: Optional[str] = None

    def __repr__(self) -> str:
        return f"OrdersCanceledEvent(order_ids={self.order_ids}, owner={self.owner[:10]}...)"


@dataclass
class TradeEvent:
    """
    Emitted when a trade goes through.

    event Trade(
        uint40 orderId,
        address makerAddress,
        bool isBuy,
        uint256 price,
        uint96 updatedSize,
        address takerAddress,
        address txOrigin,
        uint96 filledSize
    )

    Fields:
        - order_id: Order ID of the order that was filled (uint40)
        - maker_address: Address of the maker
        - is_buy: Whether it's a buy order
        - price: Price of the trade (uint256)
        - updated_size: New size of the order after the trade (uint96)
        - taker_address: Address of the taker
        - tx_origin: Transaction origin address
        - filled_size: Size filled by the taker (uint96)
    """

    order_id: int  # uint40
    maker_address: str  # address
    is_buy: bool
    price: int  # uint256
    updated_size: float  # uint96
    taker_address: str  # address
    tx_origin: str  # address
    filled_size: float  # uint96

    txhash: Optional[str] = None

    def __repr__(self) -> str:
        side = "buy" if self.is_buy else "sell"
        return (
            f"TradeEvent(order_id={self.order_id}, side={side}, price={self.price}, "
            f"filled_size={self.filled_size}, updated_size={self.updated_size})"
        )


@dataclass
class BatchUpdateMMEvent:
    """
    Event emitted when batch updating MM positions.

    event batchUpdate(bytes32[] buyCloids, bytes32[] sellCloids, bytes32[] cancelCloids)

    Fields:
        - buy_cloids: Array of client order IDs for buy orders (bytes32[])
        - sell_cloids: Array of client order IDs for sell orders (bytes32[])
        - cancel_cloids: Array of client order IDs for cancel orders (bytes32[])
        - txhash: Transaction hash for tracking
    """

    buy_cloids: list[str]  # bytes32[] - array of client order IDs for buy orders
    sell_cloids: list[str]  # bytes32[] - array of client order IDs for sell orders
    cancel_cloids: list[str]  # bytes32[] - array of client order IDs for cancel orders

    # Optional: Transaction hash for tracking
    txhash: Optional[str] = None

    def __repr__(self) -> str:
        return (
            f"BatchUpdateMMEvent(buy_cloids={len(self.buy_cloids)}, "
            f"sell_cloids={len(self.sell_cloids)}, "
            f"cancel_cloids={len(self.cancel_cloids)}, "
            f"txhash={self.txhash[:10] if self.txhash else 'None'}...)"
        )
