"""Discord bot for the Solana Wallet Tracker."""

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")

from wallet_tracker.config import Config
from wallet_tracker.matcher import WalletMatcher
from wallet_tracker.models import HoldingQuery, SearchResult, TokenInfo, VerificationResult
from wallet_tracker.token_resolver import TokenResolver

logger = logging.getLogger("wallet_tracker_bot")

MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def build_search_embed(result: SearchResult) -> discord.Embed:
    """Build a Discord embed from a SearchResult."""
    if not result.token_info:
        return discord.Embed(
            title="Token Not Found",
            description=f"Could not resolve token: `{result.query.ticker}`",
            color=discord.Color.red(),
        )

    token = result.token_info

    if result.unique_match:
        color = discord.Color.green()
        title = "Wallet Found"
    elif result.found:
        color = discord.Color.gold()
        title = f"Found {len(result.candidates)} Candidate(s)"
    else:
        color = discord.Color.red()
        title = "No Matches"

    embed = discord.Embed(title=title, color=color)

    embed.add_field(name="Token", value=f"**{token.symbol}** — {token.name}", inline=True)
    embed.add_field(name="Price", value=f"${token.price_usd:.10f}", inline=True)
    embed.add_field(
        name="Market Cap / Liquidity",
        value=f"${token.market_cap:,.0f} / ${token.liquidity_usd:,.0f}",
        inline=True,
    )
    embed.add_field(name="Mint", value=f"`{token.mint_address}`", inline=False)

    embed.add_field(
        name="Search",
        value=f"Scanned **{result.total_holders_scanned:,}** holders in **{result.search_time_ms / 1000:.1f}s**",
        inline=False,
    )

    if result.unique_match:
        wallet = result.candidates[0]
        embed.add_field(
            name="Wallet",
            value=f"```\n{wallet.address}\n```",
            inline=False,
        )
        amt = list(wallet.holdings.values())[0] if wallet.holdings else 0
        embed.add_field(name="Balance", value=f"`{amt:,.6f}`", inline=True)
    elif result.found:
        lines = []
        for i, m in enumerate(result.candidates[:10], 1):
            amt = list(m.holdings.values())[0] if m.holdings else 0
            lines.append(f"{i}. {m.address}  ({amt:,.2f})")
        wallet_list = "\n".join(lines)
        if len(result.candidates) > 10:
            wallet_list += f"\n... and {len(result.candidates) - 10} more"
        embed.add_field(
            name="Candidates",
            value=f"```\n{wallet_list}\n```",
            inline=False,
        )
        embed.set_footer(text="Use /verify with a second token to narrow results.")
    else:
        embed.add_field(
            name="Result",
            value="No wallets matched the specified token amount.\nTry adjusting the amount or check the ticker.",
            inline=False,
        )

    return embed


def build_verification_embed(result: VerificationResult) -> discord.Embed:
    """Build a Discord embed from a VerificationResult."""
    if result.verified:
        embed = discord.Embed(title="Wallet Confirmed", color=discord.Color.green())
        embed.add_field(
            name="Wallet",
            value=f"```\n{result.wallet}\n```",
            inline=False,
        )
        embed.add_field(
            name="Method",
            value="This wallet holds both specified token amounts.",
            inline=False,
        )
    elif result.confirmed_wallets:
        embed = discord.Embed(
            title=f"Multiple Matches ({len(result.confirmed_wallets)})",
            color=discord.Color.gold(),
        )
        lines = "\n".join(result.confirmed_wallets[:10])
        if len(result.confirmed_wallets) > 10:
            lines += f"\n... and {len(result.confirmed_wallets) - 10} more"
        embed.add_field(name="Wallets", value=f"```\n{lines}\n```", inline=False)
    else:
        embed = discord.Embed(title="Verification Failed", color=discord.Color.red())
        embed.add_field(
            name="Result",
            value="No wallet found holding both specified token amounts.",
            inline=False,
        )

    embed.add_field(
        name="Primary",
        value=f"`{result.primary_query.ticker}` — {result.primary_query.token_amount:,.6f} tokens ({len(result.primary_candidates)} candidates)",
        inline=True,
    )
    embed.add_field(
        name="Verification",
        value=f"`{result.verification_query.ticker}` — {result.verification_query.token_amount:,.6f} tokens ({len(result.verification_candidates)} candidates)",
        inline=True,
    )

    return embed


# ---------------------------------------------------------------------------
# Token disambiguation view (Select dropdown)
# ---------------------------------------------------------------------------

class TokenSelectView(discord.ui.View):
    """Dropdown for selecting from multiple token matches."""

    def __init__(
        self,
        candidates: list[TokenInfo],
        amount: float,
        config: Config,
        *,
        original_interaction: discord.Interaction,
    ):
        super().__init__(timeout=60.0)
        self.amount = amount
        self.config = config
        self.candidates = candidates
        self.original_interaction = original_interaction

        options = []
        for i, token in enumerate(candidates[:25]):
            mcap = f"MCap: ${token.market_cap:,.0f}" if token.market_cap else "MCap: N/A"
            liq = f"Liq: ${token.liquidity_usd:,.0f}" if token.liquidity_usd else "Liq: N/A"
            options.append(
                discord.SelectOption(
                    label=f"{token.symbol} — {token.name[:45]}",
                    description=f"{mcap} | {liq} | {token.mint_address[:24]}...",
                    value=str(i),
                )
            )

        select = discord.ui.Select(
            placeholder="Select the correct token...",
            options=options,
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        await interaction.response.defer()

        idx = int(interaction.data["values"][0])
        selected = self.candidates[idx]

        query = HoldingQuery(
            ticker=selected.symbol,
            token_amount=self.amount,
            mint_address=selected.mint_address,
        )

        matcher = WalletMatcher(self.config)
        try:
            result = await asyncio.to_thread(matcher.find_candidates, query)
        finally:
            matcher.close()

        embed = build_search_embed(result)
        await interaction.followup.send(embed=embed)
        self.stop()

    async def on_timeout(self):
        # Disable the dropdown after timeout
        for child in self.children:
            child.disabled = True
        try:
            await self.original_interaction.edit_original_response(view=self)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

class WalletTrackerBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.config = Config.load()

    async def setup_hook(self):
        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Slash commands synced to guild %s", guild_id)
        else:
            await self.tree.sync()
            logger.info("Slash commands synced globally (may take up to 1 hour to propagate)")

    async def on_ready(self):
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)


bot = WalletTrackerBot()


# ---------------------------------------------------------------------------
# Helper: resolve token input to HoldingQuery
# ---------------------------------------------------------------------------

async def _resolve_query(token: str, amount: float) -> HoldingQuery | None:
    """Resolve a token string to a HoldingQuery. Auto-picks highest liquidity."""
    token = token.strip()
    if MINT_RE.match(token):
        return HoldingQuery(
            ticker=token[:8] + "...",
            token_amount=amount,
            mint_address=token,
        )

    resolver = TokenResolver()
    try:
        candidates = await asyncio.to_thread(resolver.search_by_ticker, token.upper())
    finally:
        resolver.close()

    if not candidates:
        return None

    best = candidates[0]  # Sorted by liquidity (highest first)
    return HoldingQuery(
        ticker=best.symbol,
        token_amount=amount,
        mint_address=best.mint_address,
    )


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@bot.tree.command(name="find", description="Find wallets holding a specific amount of a token")
@app_commands.describe(
    token="Token ticker symbol (e.g. BONK) or mint address",
    amount="Exact token amount held",
)
async def cmd_find(interaction: discord.Interaction, token: str, amount: float):
    await interaction.response.defer()

    try:
        token_input = token.strip()
        is_mint = bool(MINT_RE.match(token_input))

        if is_mint:
            query = HoldingQuery(
                ticker=token_input[:8] + "...",
                token_amount=amount,
                mint_address=token_input,
            )
            matcher = WalletMatcher(bot.config)
            try:
                result = await asyncio.to_thread(matcher.find_candidates, query)
            finally:
                matcher.close()

            embed = build_search_embed(result)
            await interaction.followup.send(embed=embed)
        else:
            # Resolve ticker — may need disambiguation
            resolver = TokenResolver()
            try:
                candidates = await asyncio.to_thread(
                    resolver.search_by_ticker, token_input.upper()
                )
            finally:
                resolver.close()

            if not candidates:
                embed = discord.Embed(
                    title="Token Not Found",
                    description=f"No tokens found for `{token_input.upper()}`",
                    color=discord.Color.red(),
                )
                await interaction.followup.send(embed=embed)
                return

            if len(candidates) == 1:
                selected = candidates[0]
                query = HoldingQuery(
                    ticker=selected.symbol,
                    token_amount=amount,
                    mint_address=selected.mint_address,
                )
                matcher = WalletMatcher(bot.config)
                try:
                    result = await asyncio.to_thread(matcher.find_candidates, query)
                finally:
                    matcher.close()

                embed = build_search_embed(result)
                await interaction.followup.send(embed=embed)
            else:
                # Multiple matches — show dropdown
                view = TokenSelectView(
                    candidates, amount, bot.config,
                    original_interaction=interaction,
                )
                embed = discord.Embed(
                    title=f"Multiple tokens found for '{token_input.upper()}'",
                    description="Select the correct token from the dropdown below.",
                    color=discord.Color.gold(),
                )
                await interaction.followup.send(embed=embed, view=view)

    except Exception as e:
        logger.exception("Error in /find")
        embed = discord.Embed(
            title="Error",
            description=f"```\n{str(e)[:3900]}\n```",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed)


@bot.tree.command(
    name="verify",
    description="Verify a wallet by matching two different token holdings",
)
@app_commands.describe(
    token1="First token ticker or mint address",
    amount1="First token amount held",
    token2="Second token ticker or mint address",
    amount2="Second token amount held",
)
async def cmd_verify(
    interaction: discord.Interaction,
    token1: str,
    amount1: float,
    token2: str,
    amount2: float,
):
    await interaction.response.defer()

    try:
        q1 = await _resolve_query(token1, amount1)
        q2 = await _resolve_query(token2, amount2)

        if q1 is None or q2 is None:
            missing = []
            if q1 is None:
                missing.append(token1)
            if q2 is None:
                missing.append(token2)
            embed = discord.Embed(
                title="Token Not Found",
                description=f"Could not resolve: {', '.join(f'`{t}`' for t in missing)}",
                color=discord.Color.red(),
            )
            await interaction.followup.send(embed=embed)
            return

        matcher = WalletMatcher(bot.config)
        try:
            result = await asyncio.to_thread(
                matcher.verify_with_second_holding, q1, q2
            )
        finally:
            matcher.close()

        embed = build_verification_embed(result)
        await interaction.followup.send(embed=embed)

    except Exception as e:
        logger.exception("Error in /verify")
        embed = discord.Embed(
            title="Error",
            description=f"```\n{str(e)[:3900]}\n```",
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("ERROR: DISCORD_BOT_TOKEN not set in .env file")
        print("Get a bot token at https://discord.com/developers/applications")
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bot.run(token)


if __name__ == "__main__":
    main()
