from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.logging_setup import configure_logging

configure_logging()

_ROOT = Path(__file__).resolve().parent.parent

from google import genai  # noqa: E402

# The SDK logs a noisy "AFC is not recommended" warning on every plain
# generate_content call; we use that path deliberately.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

from src.ai.gemini import AlfredLiveSession
from src.ai.providers import build_providers
from src.brain.audit import AuditLog
from src.brain.deliberation import Deliberator
from src.brain.orchestrator import BrainLoop
from src.brain.agent import TaskAgent
from src.brain.perception import Perception
from src.brain.policy import Policy
from src.brain.reasoner import LLMReasoner
from src.brain.app_memory import AppMemory
from src.brain.skill_store import SkillStore
from src.brain.skills import SkillLibrary
from src.brain.tasks import TaskQueue
from src.brain.task_store import TaskStore
from src.config import load_settings
from src.context import build_situation
from src.tools.introspect import WhatCanYouDoTool
from src.tools.episodes_tool import EpisodesTool
from src.memory.episodes import EpisodeStore
from src.memory.learner import MemoryLearner
from src.memory.store import MemoryStore
from src.resource_mode import ResourceMode
from src.tools.resource_tool import ResourceModeTool
from src.tools.computer_screenshot import ComputerScreenshotTool
from src.tools.desktop_control import DesktopControlTool
from src.tools.ui_control import UIControlTool
from src.tools.memory_tools import ForgetTool, RecallTool, RememberTool
from src.tools.network_info import NetworkInfoTool
from src.tools.open_app import OpenAppTool
from src.tools.powershell import PowerShellTool
from src.tools.registry import ToolRegistry
from src.tools.system_info import SystemInfoTool
from src.singleton import AlreadyRunning, SingleInstance
from src.tools.task_tool import RunTaskTool, TaskStatusTool
from src.windows.child_session import ChildSessionClient
from src.windows.child_session.bootstrap import ensure_agent_running


async def main() -> None:
    settings = load_settings()

    # One Alfred at a time - two voice sessions = two voices.
    instance_lock = SingleInstance()
    try:
        instance_lock.acquire()
    except AlreadyRunning as exc:
        print(f"\n{exc}\n")
        raise SystemExit(3)  # watchdog: retry later, don't treat as clean exit

    # --------------------------------------------------------------
    # Long-term memory: persists across every future run of Alfred.
    # --------------------------------------------------------------
    gemini_client = genai.Client(api_key=settings.gemini_api_key)

    # Swappable AI backends (chat / embeddings / vision). Voice stays
    # on Gemini Live regardless. Switch with ALFRED_AI_PROVIDER.
    providers = build_providers(settings, gemini_client)
    print(f"[AI] providers: {providers.describe()}")

    store = MemoryStore(settings.memory_db_path)

    learner = MemoryLearner(
        store=store,
        chat=providers.chat,
        embedder=providers.embedder,
    )

    known_facts = len(store.all_facts())

    print(f"[Memory] {known_facts} known fact(s) loaded.")

    # --------------------------------------------------------------
    # Tools
    # --------------------------------------------------------------
    registry = ToolRegistry()

    powershell_tool = PowerShellTool()
    open_app_tool = OpenAppTool()
    system_info_tool = SystemInfoTool()
    network_info_tool = NetworkInfoTool()
    remember_tool = RememberTool(learner)
    recall_tool = RecallTool(learner)
    forget_tool = ForgetTool(learner)

    # Desktop Alfred controls: try to bring up the input/capture agent.
    agent_status = ensure_agent_running()
    print(f"[Desktop] ChildInputAgent: {agent_status}")

    child_session_client = ChildSessionClient()

    screenshot_tool = ComputerScreenshotTool(
        child_session_client,
        vision=providers.vision,
    )

    desktop_control_tool = DesktopControlTool(
        child_session_client,
        vision=providers.vision,
    )

    ui_control_tool = UIControlTool()

    # Background task agent: delegate multi-step jobs (persisted so a
    # job survives an Alfred restart).
    task_store = TaskStore(_ROOT / "alfred_tasks.sqlite3")

    skill_store = SkillStore(settings.skill_db_path)
    episode_store = EpisodeStore(settings.episode_db_path)
    # What Alfred has learned about working inside specific apps.
    app_memory = AppMemory(settings.app_db_path)
    task_queue = TaskQueue(
        store=task_store, episodes=episode_store, app_memory=app_memory,
    )

    # --------------------------------------------------------------
    # Activation: wake word + hotkey + conversation window.
    # --------------------------------------------------------------
    from src.voice import ActivationController, HotkeyListener, WakeListener

    wake_gated = settings.wake_enabled or bool(settings.hotkey)
    activation = ActivationController(
        idle_seconds=settings.listen_idle_seconds,
        always_on=not wake_gated,
    )

    # Session is needed by ResourceMode's speak callback; built now,
    # policy attached just below once every tool is registered.
    session = AlfredLiveSession(
        registry,
        store=store,
        learner=learner,
        activation=activation,
        half_duplex=settings.half_duplex,
    )

    # Game / low-resource mode.
    resource_mode = ResourceMode(
        providers=providers,
        speak=session.inject_system_prompt,
        task_queue=task_queue,
        child_client=child_session_client,
        autodetect=settings.game_autodetect,
        detect_seconds=settings.game_detect_seconds,
    )

    # A compact "what's going on right now" snapshot, shared by the voice
    # prompt, the task planner and the deliberator.
    def _situation() -> str:
        return build_situation(
            task_queue=task_queue,
            resource_mode=resource_mode,
            learner=learner,
            episodes=episode_store,
        )

    session._situation_fn = _situation

    for tool in (
        powershell_tool,
        open_app_tool,
        screenshot_tool,
        ui_control_tool,
        desktop_control_tool,
        system_info_tool,
        network_info_tool,
        remember_tool,
        recall_tool,
        forget_tool,
        RunTaskTool(task_queue),
        TaskStatusTool(task_queue),
        EpisodesTool(episode_store),
        ResourceModeTool(resource_mode),
        WhatCanYouDoTool(
            registry, settings, resource_mode=resource_mode,
            brain_enabled=settings.brain_enabled,
        ),
    ):
        registry.register(tool)

    restored = task_queue.restore()
    if restored:
        print(f"[Tasks] resumed {restored} unfinished task(s) from last run.")

    voice_policy = Policy(
        autonomy=settings.brain_autonomy,
        known_tools=set(registry.names()),
        surface="voice",
    )
    session.attach_policy(voice_policy)

    # Skill library: verified task successes are distilled into replayable
    # routines so repeat requests skip planning. Dangerous routines are
    # confirmed out loud before being saved.
    skill_library = SkillLibrary(
        skill_store,
        # voice surface: a skill counts as "dangerous" only if a step would
        # need the user's OK even when they asked for it directly.
        policy=Policy(
            autonomy=settings.brain_autonomy,
            known_tools=set(registry.names()),
            surface="voice",
        ),
        embedder=providers.embedder,
        enabled=settings.skills_enabled,
    )
    task_queue.attach_skills(skill_library)
    print(
        f"[Skills] {len(skill_store.all(include_disabled=True))} learned; "
        f"library {'on' if settings.skills_enabled else 'off'}."
    )
    _known_apps = app_memory.known_apps()
    if _known_apps:
        print(f"[Apps] know my way around: {', '.join(_known_apps[:8])}")

    session.add_background_task(resource_mode.run)

    # Offline voice loop for when the Gemini quota is exhausted.
    if settings.local_voice_fallback:
        from src.voice.local_voice import LocalVoiceSession

        session.attach_local_voice(
            lambda: LocalVoiceSession(
                providers.chat,
                registry,
                voice_policy,
                stt_model=settings.local_voice_stt_model,
                get_listening=lambda: activation.is_listening,
            )
        )

    wake_listener: WakeListener | None = None
    hotkey_listener: HotkeyListener | None = None

    if wake_gated:
        def _woken(*_a: object) -> None:
            activation.wake("wake/hotkey")
            session.notify_woken()

        if settings.wake_enabled:
            wake_listener = WakeListener(
                on_detect=_woken,
                phrase=settings.wake_phrase or None,
                model_path=settings.wake_model or None,
                threshold=settings.wake_threshold,
            )
            wake_listener.start()

        if settings.hotkey:
            hotkey_listener = HotkeyListener(
                on_press=lambda: _woken(), spec=settings.hotkey
            )
            hotkey_listener.start()

        # No point running wake detection while Alfred is already
        # active - and it stops Alfred re-triggering itself.
        def _on_listen_change(listening: bool) -> None:
            if wake_listener is None:
                return
            wake_listener.pause() if listening else wake_listener.resume()

        activation.on_state_change = _on_listen_change

    _agent_tools = set(registry.names()) - {"run_task", "task_status"}
    task_agent = TaskAgent(
        chat=providers.chat,
        plan_chat=providers.plan_chat,
        registry=registry,
        policy=Policy(
            autonomy=settings.brain_autonomy,
            known_tools=_agent_tools,
            surface="brain",
        ),
        # user-asked tasks: run ordinary steps, ask out loud on dangerous
        policy_voice=Policy(
            autonomy=settings.brain_autonomy,
            known_tools=_agent_tools,
            surface="voice",
        ),
        situation=_situation,
        learner=learner,  # for post-task reflection lessons
        app_memory=app_memory,  # per-app control knowledge
        audit=None,  # set below once the audit log exists
    )

    session.add_background_task(
        lambda: task_queue.run(
            task_agent,
            session.inject_system_prompt,
            lambda: session.session_key,
        )
    )

    # --------------------------------------------------------------
    # Proactive brain: background awareness loop. Optional; disabled
    # via ALFRED_BRAIN_ENABLED=false.
    # --------------------------------------------------------------
    audit: AuditLog | None = None
    brain: BrainLoop | None = None

    if settings.brain_enabled:
        audit = AuditLog(settings.brain_audit_path)
        task_agent._audit = audit

        perception = Perception()

        reasoner = LLMReasoner(providers.chat)

        deliberator = Deliberator(
            reasoner=reasoner,
            store=store,
            learner=learner,
            tool_catalogue=registry.gemini_declarations(),
            autonomy=settings.brain_autonomy,
            situation_fn=_situation,
        )

        policy = Policy(
            autonomy=settings.brain_autonomy,
            known_tools=set(registry.names()),
        )

        brain = BrainLoop(
            perception=perception,
            deliberator=deliberator,
            policy=policy,
            registry=registry,
            audit=audit,
            learner=learner,
            speak=session.inject_system_prompt,
            get_session_id=lambda: session.session_key,
            tick_seconds=settings.brain_tick_seconds,
            min_speak_gap_seconds=settings.brain_min_speak_gap_seconds,
            quiet_hours=settings.brain_quiet_hours,
            heartbeat_ticks=settings.brain_heartbeat_ticks,
            startup_grace_seconds=settings.brain_startup_grace_seconds,
        )

        session.attach_brain(brain)
        brain.attach_resource_mode(resource_mode)
        brain.attach_task_queue(task_queue)
        brain.attach_episodes(episode_store)
        resource_mode.attach_brain(brain)

        print(
            f"[Brain] enabled (autonomy={settings.brain_autonomy}, "
            f"tick={settings.brain_tick_seconds:.0f}s)."
        )
    else:
        print("[Brain] disabled.")

    # --------------------------------------------------------------
    # System tray presence (pause/resume awareness, open logs, quit).
    # --------------------------------------------------------------
    tray = None

    if settings.tray_enabled:
        from pathlib import Path

        from src.tray import TrayIcon

        tray = TrayIcon(
            is_brain_paused=(
                (lambda: brain.is_paused) if brain is not None else (lambda: False)
            ),
            set_brain_paused=(
                brain.set_paused if brain is not None else (lambda _v: None)
            ),
            logs_dir=Path(settings.brain_audit_path).resolve().parent,
            tooltip=f"{settings.alfred_name} — running",
            is_game_mode=lambda: resource_mode.in_game_mode,
            toggle_game_mode=resource_mode.toggle,
        )
        tray.start()

    try:
        await session.connect()

        print(
            "Alfred is listening."
        )

        print(
            "Speak naturally. "
            "Press Ctrl+C to exit."
        )

        await session.run_forever()

    except KeyboardInterrupt:
        print(
            "\nShutting down Alfred..."
        )

    finally:
        if tray is not None:
            tray.stop()

        if wake_listener is not None:
            wake_listener.stop()

        if hotkey_listener is not None:
            hotkey_listener.stop()

        await session.close()

        child_session_client.close()

        open_app_tool.close()

        if audit is not None:
            audit.close()

        task_store.close()
        skill_store.close()
        episode_store.close()
        app_memory.close()
        store.close()

        instance_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
