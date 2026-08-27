from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    alfred_name: str
    gemini_api_key: str
    gemini_live_model: str
    default_desktop: int
    user_desktop: int


def load_settings() -> Settings:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. "
            "Create a .env file from .env.example and add your API key."
        )

    return Settings(
        alfred_name=os.getenv("ALFRED_NAME", "Alfred"),
        gemini_api_key=api_key,
        gemini_live_model=os.getenv(
            "GEMINI_LIVE_MODEL",
            "gemini-3.1-flash-live-preview",
        ),
        default_desktop=int(
            os.getenv("ALFRED_DEFAULT_DESKTOP", "2")
        ),
        user_desktop=int(
            os.getenv("ALFRED_USER_DESKTOP", "1")
        ),
    )