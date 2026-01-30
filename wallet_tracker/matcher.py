"""Wallet matching engine - find wallets by token holdings."""

import time
from collections import defaultdict
from typing import Any

from .api.helius import HeliusClient
from .config import Config, get_config
from .models import (
    HolderEntry,
    HoldingQuery,
    SearchResult,
    VerificationResult,
    WalletMatch,
)
from .token_resolver import TokenResolver


class WalletMatcher:
    """
    Core wallet matching engine.

    Finds wallets by looking up all holders of a token
    and matching by exact token amount held.
    """

    def __init__(self, config: Config | None = None):
        self.config = config or get_config()
        self._resolver: TokenResolver | None = None
        self._helius: HeliusClient | None = None

    @property
    def resolver(self) -> TokenResolver:
        if self._resolver is None:
            self._resolver = TokenResolver()
        return self._resolver

    @property
    def helius(self) -> HeliusClient:
        if self._helius is None:
            self._helius = HeliusClient(self.config.helius_api_key)
        return self._helius

    def find_candidates(self, query: HoldingQuery) -> SearchResult:
        """
        Find wallets holding an exact amount of a token.

        Args:
            query: Ticker + exact token amount

        Returns:
            SearchResult with matching wallets
        """
        start_time = time.time()

        # Step 1: Resolve ticker to mint address
        # If mint_address is already set (e.g. user pasted it or selected from list), use it directly
        if query.mint_address:
            token = self.resolver.get_by_mint_address(query.mint_address)
        else:
            token = self.resolver.resolve(query.ticker)

        if not token:
            return SearchResult(
                query=query,
                token_info=None,
                candidates=[],
                search_time_ms=int((time.time() - start_time) * 1000),
            )

        query.mint_address = token.mint_address

        # Step 2: Get decimals
        supply_info = self.helius.get_token_supply(token.mint_address)
        decimals = supply_info.get("decimals", 9)
        query.decimals = decimals
        token.decimals = decimals

        # Step 3: Paginate through all holders
        raw_accounts = self.helius.get_all_holders(token.mint_address)

        # Step 4: Aggregate by owner (one wallet can have multiple token accounts)
        owner_totals: dict[str, float] = defaultdict(float)
        for acct in raw_accounts:
            owner = acct.get("owner", "")
            raw_amount = int(acct.get("amount", 0))
            ui_amount = raw_amount / (10 ** decimals)
            if owner:
                owner_totals[owner] += ui_amount

        # Step 5: Match by amount within tolerance
        tolerance = self.config.tolerances.token_amount
        target = query.token_amount
        candidates: list[WalletMatch] = []

        for owner, held in owner_totals.items():
            if target > 0 and abs(held - target) / target <= tolerance:
                match = WalletMatch(address=owner)
                match.add_holding(token.mint_address, held)
                candidates.append(match)

        elapsed = int((time.time() - start_time) * 1000)

        return SearchResult(
            query=query,
            token_info=token,
            candidates=candidates,
            total_holders_scanned=len(owner_totals),
            search_time_ms=elapsed,
        )

    def verify_with_second_holding(
        self,
        primary: HoldingQuery,
        verification: HoldingQuery,
    ) -> VerificationResult:
        """
        Verify a wallet by checking two different token holdings.

        Finds holders matching each query, then intersects.

        Args:
            primary: First token + amount
            verification: Second token + amount

        Returns:
            VerificationResult with confirmed wallet(s)
        """
        result1 = self.find_candidates(primary)
        result2 = self.find_candidates(verification)

        wallets1 = {m.address for m in result1.candidates}
        wallets2 = {m.address for m in result2.candidates}

        confirmed = sorted(wallets1 & wallets2)

        return VerificationResult(
            primary_query=primary,
            verification_query=verification,
            confirmed_wallets=confirmed,
            primary_candidates=result1.candidates,
            verification_candidates=result2.candidates,
        )

    def close(self) -> None:
        """Clean up resources."""
        if self._resolver:
            self._resolver.close()
        if self._helius:
            self._helius.close()


def find_wallet(
    ticker: str,
    token_amount: float,
    config: Config | None = None,
) -> SearchResult:
    """
    Quick function to find a wallet by token holding.

    Args:
        ticker: Token ticker symbol
        token_amount: Exact amount of tokens held
        config: Optional config override

    Returns:
        SearchResult with candidates
    """
    matcher = WalletMatcher(config)
    try:
        query = HoldingQuery(ticker=ticker, token_amount=token_amount)
        return matcher.find_candidates(query)
    finally:
        matcher.close()


def verify_wallet(
    primary: dict[str, Any],
    verification: dict[str, Any],
    config: Config | None = None,
) -> VerificationResult:
    """
    Quick function to verify a wallet using two holdings.

    Args:
        primary: Dict with ticker, token_amount
        verification: Same format
        config: Optional config override

    Returns:
        VerificationResult
    """
    q1 = HoldingQuery(ticker=primary["ticker"], token_amount=primary["token_amount"])
    q2 = HoldingQuery(ticker=verification["ticker"], token_amount=verification["token_amount"])

    matcher = WalletMatcher(config)
    try:
        return matcher.verify_with_second_holding(q1, q2)
    finally:
        matcher.close()
