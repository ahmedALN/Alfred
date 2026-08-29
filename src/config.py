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
    gemini_text_model: str
    default_desktop: int
    user_desktop: int
    memory_db_path: str

    # --- Swappable AI backends (voice always stays Gemini Live) ---
    ai_provider: str
    ai_chat_provider: str | None
    ai_embed_provider: str | None
    ai_vision_provider: str | None
    ai_chat_model: str | None
    ai_embed_model: str | None
    ai_vision_model: str | None
    ollama_base_url: str
    openai_base_url: str | None
    openai_api_key: str | None

    # --- Proactive brain (background awareness loop) ---
    brain_enabled: bool
    brain_autonomy: str
    brain_tick_seconds: float
    brain_min_speak_gap_seconds: float
    brain_quiet_hours: str | None
    brain_heartbeat_ticks: int
    brain_startup_grace_seconds: float
    brain_audit_path: str
    tray_enabled: bool

    # --- Activation: wake word / hotkey / conversation window ---
    wake_enabled: bool
    wake_phrase: str
    wake_model: str
    wake_threshold: float
    hotkey: str
    listen_idle_seconds: float
    half_duplex: bool
    voice_passphrase: str
    voice_passphrase_window: float

    # --- Game / low-resource mode ---
    game_autodetect: bool
    game_detect_seconds: float

    # --- Desktop control ---
    desktop_grid: bool

    # --- Local voice fallback (when Gemini quota is hit) ---
    local_voice_fallback: bool
    local_voice_cooldown: float
    local_voice_stt_model: str


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)

    if raw is None:
        return default

    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def load_settings() -> Settings:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. "
            "Create a .env file from .env.example and add your API key."
        )

    autonomy = os.getenv("ALFRED_BRAIN_AUTONOMY", "full").strip().lower()

    if autonomy not in ("ask", "auto_reversible", "full"):
        autonomy = "full"

    quiet_hours = os.getenv("ALFRED_BRAIN_QUIET_HOURS", "").strip() or None

    def _opt(name: str) -> str | None:
        value = os.getenv(name, "").strip()
        return value or None

    return Settings(
        alfred_name=os.getenv("ALFRED_NAME", "Alfred"),
        gemini_api_key=api_key,
        gemini_live_model=os.getenv(
            "GEMINI_LIVE_MODEL",
            "gemini-3.1-flash-live-preview",
        ),
        gemini_text_model=os.getenv(
            "GEMINI_TEXT_MODEL",
            "gemini-flash-latest",
        ),
        default_desktop=int(
            os.getenv("ALFRED_DEFAULT_DESKTOP", "2")
        ),
        user_desktop=int(
            os.getenv("ALFRED_USER_DESKTOP", "1")
        ),
        memory_db_path=os.getenv(
            "ALFRED_MEMORY_DB",
            "alfred_memory.sqlite3",
        ),
        brain_enabled=_get_bool("ALFRED_BRAIN_ENABLED", True),
        brain_autonomy=autonomy,
        brain_tick_seconds=_get_float("ALFRED_BRAIN_TICK_SECONDS", 90.0),
        brain_min_speak_gap_seconds=_get_float(
            "ALFRED_BRAIN_MIN_SPEAK_GAP", 600.0
        ),
        brain_quiet_hours=quiet_hours,
        brain_heartbeat_ticks=_get_int("ALFRED_BRAIN_HEARTBEAT_TICKS", 0),
        brain_startup_grace_seconds=_get_float(
            "ALFRED_BRAIN_STARTUP_GRACE", 60.0
        ),
        brain_audit_path=os.getenv(
            "ALFRED_BRAIN_AUDIT_DB",
            "alfred_brain_audit.sqlite3",
        ),
        tray_enabled=_get_bool("ALFRED_TRAY_ENABLED", True),
        wake_enabled=_get_bool("ALFRED_WAKE_ENABLED", True),
        wake_phrase=os.getenv("ALFRED_WAKE_PHRASE", "").strip().lower(),
        wake_model=os.getenv("ALFRED_WAKE_MODEL", "").strip(),
        wake_threshold=_get_float("ALFRED_WAKE_THRESHOLD", 0.5),
        hotkey=os.getenv("ALFRED_HOTKEY", "ctrl+alt+k").strip().lower(),
        listen_idle_seconds=_get_float("ALFRED_LISTEN_IDLE_SECONDS", 30.0),
        half_duplex=_get_bool("ALFRED_HALF_DUPLEX", True),
        voice_passphrase=os.getenv("ALFRED_VOICE_PASSPHRASE", "").strip().lower(),
        voice_passphrase_window=_get_float(
            "ALFRED_VOICE_PASSPHRASE_WINDOW", 300.0
        ),
        game_autodetect=_get_bool("ALFRED_GAME_AUTODETECT", True),
        game_detect_seconds=_get_float("ALFRED_GAME_DETECT_SECONDS", 30.0),
        desktop_grid=_get_bool("ALFRED_DESKTOP_GRID", True),
        local_voice_fallback=_get_bool("ALFRED_LOCAL_VOICE_FALLBACK", True),
        local_voice_cooldown=_get_float("ALFRED_LOCAL_VOICE_COOLDOWN", 300.0),
        local_voice_stt_model=os.getenv(
            "ALFRED_LOCAL_VOICE_STT_MODEL", "base.en"
        ),
        ai_provider=os.getenv("ALFRED_AI_PROVIDER", "gemini").strip().lower()
        or "gemini",
        ai_chat_provider=_opt("ALFRED_AI_CHAT_PROVIDER"),
        ai_embed_provider=_opt("ALFRED_AI_EMBED_PROVIDER"),
        ai_vision_provider=_opt("ALFRED_AI_VISION_PROVIDER"),
        ai_chat_model=_opt("ALFRED_AI_CHAT_MODEL"),
        ai_embed_model=_opt("ALFRED_AI_EMBED_MODEL"),
        ai_vision_model=_opt("ALFRED_AI_VISION_MODEL"),
        ollama_base_url=os.getenv(
            "ALFRED_OLLAMA_BASE_URL", "http://localhost:11434"
        ),
        openai_base_url=_opt("ALFRED_OPENAI_BASE_URL"),
        openai_api_key=_opt("ALFRED_OPENAI_API_KEY"),
    )
