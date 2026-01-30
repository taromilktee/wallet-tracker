"""Data models for the wallet tracker."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TokenInfo:
    """Information about a token."""
    mint_address: str
    symbol: str
    name: str
    price_usd: float = 0.0
    market_cap: float = 0.0
    fdv: float = 0.0
    liquidity_usd: float = 0.0
    volume_24h: float = 0.0
    supply: float = 0.0
    decimals: int = 9
    pair_address: str | None = None
    dex_id: str | None = None

    @classmethod
    def from_dexscreener(cls, data: dict[str, Any]) -> "TokenInfo":
        """Create TokenInfo from DexScreener pair data."""
        base_token = data.get("baseToken", {})
        return cls(
            mint_address=base_token.get("address", ""),
            symbol=base_token.get("symbol", ""),
            name=base_token.get("name", ""),
            price_usd=float(data.get("priceUsd", 0) or 0),
            market_cap=float(data.get("marketCap", 0) or 0),
            fdv=float(data.get("fdv", 0) or 0),
            liquidity_usd=float(data.get("liquidity", {}).get("usd", 0) or 0),
            volume_24h=float(data.get("volume", {}).get("h24", 0) or 0),
            pair_address=data.get("pairAddress"),
            dex_id=data.get("dexId"),
        )


@dataclass
class HolderEntry:
    """A single token holder from Helius getTokenAccounts."""
    owner: str              # Wallet address
    token_account: str      # Token account address
    amount: int             # Raw amount (before decimals)
    ui_amount: float        # Human-readable amount (after decimals)

    @classmethod
    def from_helius(cls, data: dict[str, Any], decimals: int = 9) -> "HolderEntry":
        raw_amount = int(data.get("amount", 0))
        return cls(
            owner=data.get("owner", ""),
            token_account=data.get("address", ""),
            amount=raw_amount,
            ui_amount=raw_amount / (10 ** decimals),
        )


@dataclass
class HoldingQuery:
    """User-provided holding to search for."""
    ticker: str
    token_amount: float     # Exact token amount held

    # Resolved after token lookup
    mint_address: str | None = None
    decimals: int = 9


@dataclass
class WalletMatch:
    """A wallet that matches one or more holding queries."""
    address: str
    holdings: dict[str, float] = field(default_factory=dict)  # mint -> amount

    def add_holding(self, mint: str, amount: float) -> None:
        self.holdings[mint] = amount


@dataclass
class SearchResult:
    """Result of a single token holder search."""
    query: HoldingQuery
    token_info: TokenInfo | None
    candidates: list[WalletMatch]
    total_holders_scanned: int = 0
    search_time_ms: int = 0

    @property
    def found(self) -> bool:
        return len(self.candidates) > 0

    @property
    def unique_match(self) -> bool:
        return len(self.candidates) == 1


@dataclass
class VerificationResult:
    """Result of wallet verification using two holdings."""
    primary_query: HoldingQuery
    verification_query: HoldingQuery
    confirmed_wallets: list[str]
    primary_candidates: list[WalletMatch]
    verification_candidates: list[WalletMatch]

    @property
    def verified(self) -> bool:
        return len(self.confirmed_wallets) == 1

    @property
    def wallet(self) -> str | None:
        return self.confirmed_wallets[0] if self.verified else None
