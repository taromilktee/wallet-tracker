"""Configuration management for the wallet tracker."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


# Load .env file from project root
_project_root = Path(__file__).parent.parent
_env_path = _project_root / ".env"
_config_path = _project_root / "config.json"

load_dotenv(_env_path)

# Defaults (used when config.json is missing or incomplete)
_DEFAULTS = {
    "token_amount_pct": 0.001,   # 0.1% tolerance on token amount
    "max_holder_pages": 50,      # max pages of holders to scan
}


def _load_config_json() -> dict:
    """Load config.json from project root. Returns empty dict if missing."""
    if not _config_path.exists():
        return {}
    try:
        with open(_config_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[WARN] Could not parse config.json: {e}  -- using defaults")
        return {}


@dataclass
class Tolerances:
    """Matching tolerances for holder identification."""
    # Token amount tolerance (fraction, e.g. 0.001 = 0.1%)
    token_amount: float = _DEFAULTS["token_amount_pct"]


@dataclass
class Config:
    """Application configuration."""
    # API Keys
    helius_api_key: str

    # Tolerances
    tolerances: Tolerances

    # Search settings
    max_holder_pages: int = _DEFAULTS["max_holder_pages"]

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from .env + config.json."""
        helius_key = os.getenv("HELIUS_API_KEY", "")

        if not helius_key:
            raise ValueError(
                "HELIUS_API_KEY environment variable is required.\n"
                "Get your free API key at https://helius.dev (1M credits/month free)"
            )

        # Read user-editable config.json
        user_cfg = _load_config_json()
        tol_cfg = user_cfg.get("tolerances", {})

        tolerances = Tolerances(
            token_amount=float(tol_cfg.get(
                "token_amount_pct", _DEFAULTS["token_amount_pct"]
            )),
        )

        max_pages = int(user_cfg.get(
            "max_holder_pages", _DEFAULTS["max_holder_pages"]
        ))

        return cls(
            helius_api_key=helius_key,
            tolerances=tolerances,
            max_holder_pages=max_pages,
        )

    @classmethod
    def load(cls) -> "Config":
        """Load configuration, with helpful error messages."""
        try:
            return cls.from_env()
        except ValueError as e:
            print(f"\n[ERROR] Configuration Error:\n{e}\n")
            print("Setup instructions:")
            print("1. Copy .env.example to .env")
            print("2. Sign up at https://helius.dev")
            print("3. Get your API key from the dashboard")
            print("4. Add it to .env: HELIUS_API_KEY=your_key_here\n")
            raise


def get_config() -> Config:
    """Get the application configuration (singleton pattern)."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


_config: Config | None = None
