from dataclasses import dataclass

from web3 import AsyncWeb3, AsyncHTTPProvider, Web3

from src.utils import load_abi


@dataclass
class L2Book:
    block_number: int
    bids: list[tuple[int, int]]  # [(price, size), ...] descending by price
    asks: list[tuple[int, int]]  # [(price, size), ...] ascending by price


class MarketOrderbook:
    def __init__(self, rpc_url: str, orderbook_address: str):
        self.rpc_url = rpc_url
        self.orderbook_address = orderbook_address
        self.w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
        orderbook_abi = load_abi("orderbook")
        self.contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(orderbook_address),
            abi=orderbook_abi,
        )

    async def get_l2_book(self) -> L2Book:
        raw: bytes = await self.contract.functions.getL2Book().call()

        offset = 0
        block_number = int.from_bytes(raw[offset : offset + 32], "big")
        offset += 32

        bids: list[tuple[int, int]] = []
        asks: list[tuple[int, int]] = []

        # Parse bids until zero-price separator
        while offset + 64 <= len(raw):
            price = int.from_bytes(raw[offset : offset + 32], "big")
            size = int.from_bytes(raw[offset + 32 : offset + 64], "big")
            offset += 64
            if price == 0:
                break
            bids.append((price, size))

        # Parse asks from remaining data
        while offset + 64 <= len(raw):
            price = int.from_bytes(raw[offset : offset + 32], "big")
            size = int.from_bytes(raw[offset + 32 : offset + 64], "big")
            offset += 64
            if price == 0:
                break
            asks.append((price, size))

        return L2Book(block_number=block_number, bids=bids, asks=asks)
