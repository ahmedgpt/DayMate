"""Environment-driven configuration. Nothing here has a secret default."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_path: str = "./data/daymate.db"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    mcp_url: str = "http://127.0.0.1:8000/mcp"
    mcp_auth_token: str = ""
    groq_api_key: str = ""
    llm_model: str = "openai/gpt-oss-120b"
    telegram_bot_token: str = ""
    default_location: str = ""
    reminder_lease_seconds: int = 300
    reminder_max_attempts: int = 5
    weather_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        env = os.environ
        d = cls()
        return cls(
            database_path=env.get("DATABASE_PATH", d.database_path),
            mcp_host=env.get("MCP_HOST", d.mcp_host),
            mcp_port=int(env.get("MCP_PORT", d.mcp_port)),
            mcp_url=env.get("MCP_URL", d.mcp_url),
            mcp_auth_token=env.get("MCP_AUTH_TOKEN", ""),
            groq_api_key=env.get("GROQ_API_KEY", ""),
            llm_model=env.get("LLM_MODEL", d.llm_model),
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            default_location=env.get("DEFAULT_LOCATION", ""),
            reminder_lease_seconds=int(env.get("REMINDER_LEASE_SECONDS", d.reminder_lease_seconds)),
            reminder_max_attempts=int(env.get("REMINDER_MAX_ATTEMPTS", d.reminder_max_attempts)),
        )

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise SystemExit(f"Missing required configuration: {', '.join(m.upper() for m in missing)} (see .env.example)")
