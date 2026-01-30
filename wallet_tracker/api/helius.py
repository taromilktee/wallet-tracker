"""Helius API client for token holder lookups."""

from typing import Any

import httpx

from .base import BaseAPIClient, APIError


class HeliusClient(BaseAPIClient):
    """
    Client for Helius API (FREE tier: 1M credits/month).

    Primary use: getTokenAccounts to find all holders of a token
    and match by exact balance.
    """

    BASE_URL = "https://api.helius.xyz"
    RPC_BASE_URL = "https://mainnet.helius-rpc.com"

    def __init__(self, api_key: str):
        super().__init__(base_url=self.BASE_URL)
        self.api_key = api_key
        self.rpc_url = f"{self.RPC_BASE_URL}/?api-key={api_key}"

    def rpc_request(self, method: str, params: Any) -> Any:
        """
        Make a JSON-RPC request to Helius RPC endpoint.

        Args:
            method: RPC method name
            params: Method parameters (dict or list)

        Returns:
            RPC result
        """
        payload = {
            "jsonrpc": "2.0",
            "id": "wallet-tracker",
            "method": method,
            "params": params,
        }

        with httpx.Client(timeout=30.0) as client:
            response = client.post(self.rpc_url, json=payload)
            data = response.json()

            if "error" in data:
                raise APIError(f"RPC Error: {data['error']}")

            return data.get("result")

    def get_token_accounts(
        self,
        mint: str,
        page: int = 1,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Get all token accounts for a mint address.

        Each account includes: address, owner, amount, decimals.

        Args:
            mint: Token mint address
            page: Page number (starts at 1)
            limit: Max results per page (max 1000)

        Returns:
            List of token account dicts
        """
        result = self.rpc_request(
            "getTokenAccounts",
            {
                "page": page,
                "limit": min(limit, 1000),
                "displayOptions": {},
                "mint": mint,
            },
        )

        if result and "token_accounts" in result:
            return result["token_accounts"]
        return []

    def get_all_holders(
        self,
        mint: str,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Paginate through ALL token holders for a mint.

        Args:
            mint: Token mint address
            max_pages: Safety limit on pages to fetch

        Returns:
            List of all token account dicts
        """
        all_accounts: list[dict[str, Any]] = []
        page = 1

        while page <= max_pages:
            accounts = self.get_token_accounts(mint, page=page, limit=1000)
            if not accounts:
                break
            all_accounts.extend(accounts)
            if len(accounts) < 1000:
                break
            page += 1

        return all_accounts

    def get_token_supply(self, mint: str) -> dict[str, Any]:
        """Get token supply info including decimals."""
        result = self.rpc_request(
            "getTokenSupply",
            [mint],
        )
        return result.get("value", {}) if result else {}
