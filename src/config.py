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
    skill_db_path: str
    skills_enabled: bool
    episode_db_path: str
    app_db_path: str

    # --- Swappable AI backends (voice always stays Gemini Live) ---
    ai_provider: str
    ai_chat_provider: str | None
    ai_embed_provider: str | None
    ai_vision_provider: str | None
    ai_chat_model: str | None
    ai_embed_model: str | None
    ai_vision_model: str | None
    ai_plan_provider: str
    ai_plan_model: str
    ai_plan_fallbacks: list[str]
    ollama_base_url: str
    openai_base_url: str | None
    openai_api_key: str | None

    # --- Proactive brain (background awareness loop) ---
    brain_enabled: bool
    brain_autonomy: str
    brain_tick_seconds: float
    # Whether the brain's unprompted observations are spoken aloud.
    brain_speak_proactive: bool
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
    # The interface is opt-in: this summons it, nothing else does.
    interface_hotkey: str
    listen_idle_seconds: float
    half_duplex: bool
    voice_passphrase: str
    voice_passphrase_window: float

    # --- Messaging Alfred from a phone ---
    whatsapp_token: str
    whatsapp_phone_id: str
    whatsapp_app_secret: str
    whatsapp_verify_token: str
    whatsapp_allowed: tuple[str, ...]
    webhook_port: int
    webhook_path: str

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
        skill_db_path=os.getenv(
            "ALFRED_SKILL_DB",
            "alfred_skills.sqlite3",
        ),
        skills_enabled=_get_bool("ALFRED_SKILLS_ENABLED", True),
        episode_db_path=os.getenv(
            "ALFRED_EPISODE_DB",
            "alfred_episodes.sqlite3",
        ),
        app_db_path=os.getenv(
            "ALFRED_APP_DB",
            "alfred_apps.sqlite3",
        ),
        brain_enabled=_get_bool("ALFRED_BRAIN_ENABLED", True),
        brain_speak_proactive=_get_bool(
            "ALFRED_BRAIN_SPEAK_PROACTIVE", False
        ),
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
        interface_hotkey=os.getenv(
            "ALFRED_INTERFACE_HOTKEY", "ctrl+alt+i"
        ).strip().lower(),
        listen_idle_seconds=_get_float("ALFRED_LISTEN_IDLE_SECONDS", 30.0),
        half_duplex=_get_bool("ALFRED_HALF_DUPLEX", True),
        voice_passphrase=os.getenv("ALFRED_VOICE_PASSPHRASE", "").strip().lower(),
        voice_passphrase_window=_get_float(
            "ALFRED_VOICE_PASSPHRASE_WINDOW", 300.0
        ),
        whatsapp_token=os.getenv("ALFRED_WHATSAPP_TOKEN", "").strip(),
        whatsapp_phone_id=os.getenv(
            "ALFRED_WHATSAPP_PHONE_ID", ""
        ).strip(),
        whatsapp_app_secret=os.getenv(
            "ALFRED_WHATSAPP_APP_SECRET", ""
        ).strip(),
        whatsapp_verify_token=os.getenv(
            "ALFRED_WHATSAPP_VERIFY_TOKEN", ""
        ).strip(),
        whatsapp_allowed=tuple(
            part.strip()
            for part in os.getenv("ALFRED_WHATSAPP_ALLOWED", "").split(",")
            if part.strip()
        ),
        webhook_port=_get_int("ALFRED_WEBHOOK_PORT", 8770),
        webhook_path=os.getenv("ALFRED_WEBHOOK_PATH", "/webhook").strip(),
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
        # The fast model plans, and the big one backs it up.
        #
        # It used to be the other way round, because a 120B planned
        # better. Measured again after a dozen accuracy fixes, the gap
        # had closed: the same eight-case battery passed 8/8 either way,
        # with the same number of failed tool calls, and the everyday
        # jobs went 18.9s -> 4.7s, 22.4s -> 6.0s, 8.8s -> 4.1s. Most of
        # what the big model was buying had been bought instead by
        # forgiving arguments, a screen snapshot, and knowing how to
        # close a window.
        ai_plan_provider=os.getenv(
            "ALFRED_AI_PLAN_PROVIDER", "gemini"
        ).strip().lower() or "openai",
        ai_plan_model=os.getenv(
            "ALFRED_AI_PLAN_MODEL", "gemini-flash-lite-latest"
        ).strip(),
        ai_plan_fallbacks=[
            p.strip().lower()
            for p in os.getenv("ALFRED_AI_PLAN_FALLBACKS", "openai,gemini,ollama").split(",")
            if p.strip()
        ],
        ollama_base_url=os.getenv(
            "ALFRED_OLLAMA_BASE_URL", "http://localhost:11434"
        ),
        openai_base_url=os.getenv(
            "ALFRED_OPENAI_BASE_URL",
            "https://integrate.api.nvidia.com/v1",
        ).strip() or None,
        openai_api_key=_opt("ALFRED_OPENAI_API_KEY"),
    )
