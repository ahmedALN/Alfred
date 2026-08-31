from __future__ import annotations

import asyncio
import os
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
from src.brain.limitations import LimitationStore
from src.brain.activity import ActivityCollector, ActivityLog, watching
from src.brain.signals import default_collectors
from src.brain.mailwatch import MailCollector
from src.brain.schedule import ScheduleStore
from src.brain.world import World, refresh as refresh_world
from src.brain.worldwatch import WorldCollector
from src.mail import Gmail
from src.workspace import GoogleAccount
from src.workspace.calendar import Calendar
from src.workspace.classroom import Classroom
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
from src.tools.web import WebTool
from src.singleton import AlreadyRunning, SingleInstance
from src.tools.calendar_tool import CalendarTool
from src.tools.diary_tool import DiaryTool
from src.tools.world_tool import WorldTool
from src.tools.classroom_tool import ClassroomTool
from src.tools.mail_tool import MailTool
from src.tools.schedule_tool import ScheduleTool
from src.tools.task_tool import RunTaskTool, SteerTaskTool, TaskStatusTool
from src.windows.child_session import ChildSessionClient
from src.windows.isolated_desktop import IsolatedDesktop
from src.windows.uia_remote import RemoteUia
from src.windows.session_router import ROUTER as session_router
from src.windows.child_session.bootstrap import ensure_agent_running


def _build_personal_whatsapp(
    settings, task_queue, status_tool, session, chat, screenshot=None,
    eyes=None,
):
    """The linked-device route: Alfred messages your own chat.

    Nothing is exposed - it connects outward like WhatsApp Web. Pair it
    once with: python -m src.whatsapp pair
    """
    from src.messaging.capture import ScreenShare
    from src.messaging.reply import Conversation
    from src.messaging.router import MessageRouter
    from src.messaging.whatsapp_personal import PersonalWhatsApp

    owner = settings.whatsapp_allowed[0]
    channel = PersonalWhatsApp(session, owner)

    talk = Conversation(
        chat,
        lambda goal: task_queue.submit(goal, source="voice"),
        screen=ScreenShare(screenshot, channel.send_file) if screenshot else None,
        steer=task_queue.steer,
        running=lambda: (
            task_queue.current().goal if task_queue.current() else ""
        ),
        eyes=eyes,
    )

    router = MessageRouter(
        channel,
        list(settings.whatsapp_allowed),
        lambda text: task_queue.submit(text, source="voice"),
        status=_status_reporter(status_tool),
        converse=talk.handle,
    )
    channel.start(router.handle)

    print(f"[Message] WhatsApp linked to {owner} - messaging your own chat.")
    return router


def _status_reporter(status_tool):
    def status() -> str:
        try:
            report = status_tool.execute({})
            running = report.get("running") or []
            recent = report.get("recent") or []
            if running:
                return "Working on: " + "; ".join(
                    str(t.get("goal", ""))[:80] for t in running[:2]
                )
            if recent:
                last = recent[0]
                return (
                    f"Nothing running. Last: {str(last.get('goal',''))[:70]} "
                    f"({last.get('status','')})"
                )
        except Exception:  # noqa: BLE001
            pass
        return "Nothing running."

    return status


def _build_phone_channel(
    settings, task_queue, status_tool, chat, screenshot=None, eyes=None,
):
    """Bring up the WhatsApp channel, if it has been set up.

    Returns the router, or None. Everything about this is optional: an
    unconfigured Alfred behaves exactly as before.
    """
    from src.messaging.reply import Conversation
    from src.messaging.router import MessageRouter

    # Two ways in, and the personal one wins when it has been linked,
    # because it needs no business number and nothing exposed.
    session = _ROOT / os.getenv(
        "ALFRED_WHATSAPP_SESSION", "alfred_whatsapp.sqlite3"
    )
    if session.exists() and settings.whatsapp_allowed:
        return _build_personal_whatsapp(
            settings, task_queue, status_tool, session, chat, screenshot,
            eyes,
        )

    if not (settings.whatsapp_token and settings.whatsapp_phone_id):
        return None

    from src.messaging.server import WebhookServer
    from src.messaging.whatsapp import WhatsAppChannel

    if not settings.whatsapp_allowed:
        print(
            "[Message] WhatsApp is configured but ALFRED_WHATSAPP_ALLOWED "
            "is empty, so every message would be refused. Set it to your "
            "own number."
        )
        return None

    if not settings.whatsapp_app_secret:
        print(
            "[Message] WhatsApp needs ALFRED_WHATSAPP_APP_SECRET - without "
            "it there is no way to tell a real message from a forged one, "
            "and this channel can run anything on this machine."
        )
        return None

    channel = WhatsAppChannel(
        settings.whatsapp_token,
        settings.whatsapp_phone_id,
        app_secret=settings.whatsapp_app_secret,
        verify_token=settings.whatsapp_verify_token,
    )

    talk = Conversation(
        chat,
        lambda goal: task_queue.submit(goal, source="voice"),
        steer=task_queue.steer,
        running=lambda: (
            task_queue.current().goal if task_queue.current() else ""
        ),
    )

    router = MessageRouter(
        channel,
        list(settings.whatsapp_allowed),
        lambda text: task_queue.submit(text, source="voice"),
        status=_status_reporter(status_tool),
        converse=talk.handle,
    )
    channel.start(router.handle)

    server = WebhookServer(
        channel, port=settings.webhook_port, path=settings.webhook_path
    )
    if not server.start():
        return None

    print(
        f"[Message] WhatsApp ready for {len(settings.whatsapp_allowed)} "
        "number(s). Point the Meta webhook at your tunnel."
    )
    return router


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
    open_app_tool = OpenAppTool()  # isolation wired in below
    web_tool = WebTool()
    system_info_tool = SystemInfoTool()
    network_info_tool = NetworkInfoTool()
    remember_tool = RememberTool(learner)
    recall_tool = RecallTool(learner)
    forget_tool = ForgetTool(learner)

    # Desktop Alfred controls: try to bring up the input/capture agent.
    agent_status = ensure_agent_running()
    print(f"[Desktop] ChildInputAgent: {agent_status}")

    child_session_client = ChildSessionClient()

    # Alfred's own desktop, brought up on demand when a task says
    # "without disturbing me".
    isolated_desktop = IsolatedDesktop(
        # One connection to that session, shared with the tools - the
        # agent there refuses a second.
        client_provider=lambda: session_router.client_for("child"),
    )

    screenshot_tool = ComputerScreenshotTool(
        child_session_client,
        vision=providers.vision,
        router=session_router,
    )

    desktop_control_tool = DesktopControlTool(
        child_session_client,
        vision=providers.vision,
        router=session_router,
    )

    # The accessibility layer only reaches the session it runs in, so
    # ui_control gets both: the local one for the user's screen, and the
    # in-session agent for Alfred's private desktop.
    ui_control_tool = UIControlTool(
        router=session_router,
        remote=RemoteUia(session_router.client),
        # Reads the labels off a screenshot when an app draws its own
        # buttons and names none of them.
        vision=providers.vision,
    )

    # Background task agent: delegate multi-step jobs (persisted so a
    # job survives an Alfred restart).
    # What Alfred owes, and when.
    schedule = ScheduleStore(_ROOT / "alfred_schedule.sqlite3")
    activity = ActivityLog(_ROOT / "alfred_activity.sqlite3")
    # People, deadlines, and what you are actually working on -
    # assembled from the mail, calendar and coursework Alfred can
    # already read, so the proactive loop has something about YOU.
    world = World(_ROOT / "alfred_world.sqlite3")

    # One Google sign-in, three services. Read and add; never send,
    # never delete. See src/workspace/account.py for which half of that
    # Google enforces and which half Alfred does.
    google = GoogleAccount(
        secrets=_ROOT / os.getenv("ALFRED_GMAIL_SECRETS", "gmail_client.json"),
        token=_ROOT / os.getenv("ALFRED_GMAIL_TOKEN", "gmail_token.json"),
    )
    mail = Gmail(google)
    diary = Calendar(google)
    classroom = Classroom(google)

    task_store = TaskStore(_ROOT / "alfred_tasks.sqlite3")

    skill_store = SkillStore(settings.skill_db_path)
    episode_store = EpisodeStore(settings.episode_db_path)
    # What Alfred has learned about working inside specific apps.
    app_memory = AppMemory(settings.app_db_path)
    # What Alfred has learned about controls the accessibility layer
    # cannot name - see ui_control's learn_control / unnamed.
    ui_control_tool._memory = app_memory

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
            world=world,
        )

    session._situation_fn = _situation

    # Shared with the phone channel, which reports status.
    task_status_tool = TaskStatusTool(task_queue)

    for tool in (
        powershell_tool,
        open_app_tool,
        screenshot_tool,
        ui_control_tool,
        web_tool,
        desktop_control_tool,
        system_info_tool,
        network_info_tool,
        remember_tool,
        recall_tool,
        forget_tool,
        RunTaskTool(task_queue),
        SteerTaskTool(task_queue),
        DiaryTool(providers.fast_chat, _ROOT),
        WorldTool(world),
        ScheduleTool(schedule),
        MailTool(mail),
        CalendarTool(diary),
        ClassroomTool(classroom),
        task_status_tool,
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
    session.attach_isolation(isolated_desktop, session_router)
    open_app_tool._router = session_router
    open_app_tool._isolated = isolated_desktop

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
    task_queue.attach_isolated_desktop(isolated_desktop, session_router)
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
    # Walls Alfred has run into before, and what got past them.
    limitations = LimitationStore(_ROOT / "alfred_limitations.sqlite3")

    task_agent = TaskAgent(
        # Executing is where the tool calls are emitted, and that is a
        # function-calling job: the local 4B model plans a sensible step
        # and then fails to call anything, worse the more tools there
        # are. It gets the same strong chain as planning.
        chat=providers.plan_chat,
        plan_chat=providers.plan_chat,
        fast_chat=providers.fast_chat,
        # Verifying is a yes/no judgement over one substep, so it stays
        # on the fast local model - the reason it was split out.
        verify_chat=providers.chat,
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
        limitations=limitations,  # what it keeps running into
        audit=None,  # set below once the audit log exists
    )

    # --------------------------------------------------------------
    # Messaging Alfred from a phone. Optional; off unless configured.
    # --------------------------------------------------------------
    def _screen_png() -> bytes:
        """The screen the person means.

        Asking to see your PC means the screen you sit in front of, and
        grabbing that directly is both instant and unarguable - no
        capture session, no frame pool, nothing that could hand back
        something from a minute ago. The agent is asked only when Alfred
        is working on its own desktop, where a direct grab would show
        the wrong screen entirely.
        """
        if session_router.isolated:
            try:
                png = session_router.client_for(
                    "child", fallback=True
                ).screenshot().png_bytes
                if png:
                    return png
            except Exception:  # noqa: BLE001
                pass

        import io

        from PIL import ImageGrab

        buffer = io.BytesIO()
        ImageGrab.grab(all_screens=True).save(buffer, format="PNG")
        return buffer.getvalue()

    phone = _build_phone_channel(
        settings, task_queue, task_status_tool, providers.plan_chat,
        _screen_png, providers.vision,
    )

    async def announce(text: str) -> None:
        """Say it in the room, and send it to the phone."""
        if phone is not None:
            # The spoken form carries a marker for the voice model.
            phone.notify(text.replace("(System: proactive)", "").strip())
        await session.inject_system_prompt(text)

    session.add_background_task(
        lambda: task_queue.run(
            task_agent,
            announce,
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

        # What the machine is doing, and - if you allow it - what you
        # are. Every collector before this one watched the plumbing,
        # which left the proactive loop nothing personal to be
        # proactive about.
        watchers = default_collectors()
        # A lapsed mailbox link is silent otherwise: the inbox stops
        # being mentioned and nothing says why.
        watchers.append(MailCollector(mail))
        watchers.append(WorldCollector(
            world,
            refresh=lambda: refresh_world(
                world, classroom=classroom, calendar=diary,
                mail=mail, activity=activity,
            ),
        ))
        if watching():
            watchers.append(ActivityCollector(activity))
            print("[Brain] watching apps and window titles (ALFRED_WATCH_ME=false to stop).")
        perception = Perception(watchers)

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
            # announce, not inject: the brain said everything into the
            # room and nowhere else, so a reminder set for six in the
            # evening reached you only if you happened to be sitting
            # here at six. Which is the one case where you did not need
            # reminding.
            speak=announce,
            get_session_id=lambda: session.session_key,
            tick_seconds=settings.brain_tick_seconds,
            min_speak_gap_seconds=settings.brain_min_speak_gap_seconds,
            quiet_hours=settings.brain_quiet_hours,
            heartbeat_ticks=settings.brain_heartbeat_ticks,
            startup_grace_seconds=settings.brain_startup_grace_seconds,
            speak_proactive=settings.brain_speak_proactive,
        )

        session.attach_brain(brain)
        brain.attach_resource_mode(resource_mode)
        brain.attach_task_queue(task_queue)
        brain.attach_schedule(schedule)
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

        session_router.close()
        isolated_desktop.shutdown()
        task_store.close()
        skill_store.close()
        episode_store.close()
        app_memory.close()
        store.close()

        instance_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
