from __future__ import annotations

import asyncio

from google import genai

from src.ai.gemini import AlfredLiveSession
from src.ai.providers import build_providers
from src.brain.audit import AuditLog
from src.brain.deliberation import Deliberator
from src.brain.orchestrator import BrainLoop
from src.brain.agent import TaskAgent
from src.brain.perception import Perception
from src.brain.policy import Policy
from src.brain.reasoner import LLMReasoner
from src.brain.tasks import TaskQueue
from src.config import load_settings
from src.memory.learner import MemoryLearner
from src.memory.store import MemoryStore
from src.tools.computer_screenshot import ComputerScreenshotTool
from src.tools.desktop_control import DesktopControlTool
from src.tools.memory_tools import RecallTool, RememberTool
from src.tools.network_info import NetworkInfoTool
from src.tools.open_app import OpenAppTool
from src.tools.powershell import PowerShellTool
from src.tools.registry import ToolRegistry
from src.tools.system_info import SystemInfoTool
from src.tools.task_tool import RunTaskTool, TaskStatusTool
from src.windows.child_session import ChildSessionClient
from src.windows.child_session.bootstrap import ensure_agent_running


async def main() -> None:
    settings = load_settings()

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

    # Background task agent: delegate multi-step jobs.
    task_queue = TaskQueue()

    for tool in (
        powershell_tool,
        open_app_tool,
        screenshot_tool,
        desktop_control_tool,
        system_info_tool,
        network_info_tool,
        remember_tool,
        recall_tool,
        RunTaskTool(task_queue),
        TaskStatusTool(task_queue),
    ):
        registry.register(tool)

    voice_policy = Policy(
        autonomy=settings.brain_autonomy,
        known_tools=set(registry.names()),
        surface="voice",
    )

    session = AlfredLiveSession(
        registry,
        store=store,
        learner=learner,
        policy=voice_policy,
    )

    task_agent = TaskAgent(
        chat=providers.chat,
        registry=registry,
        policy=Policy(
            autonomy=settings.brain_autonomy,
            # the task agent must not enqueue more tasks
            known_tools=set(registry.names()) - {"run_task", "task_status"},
            surface="brain",
        ),
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
        )

        session.attach_brain(brain)

        print(
            f"[Brain] enabled (autonomy={settings.brain_autonomy}, "
            f"tick={settings.brain_tick_seconds:.0f}s)."
        )
    else:
        print("[Brain] disabled.")

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
        await session.close()

        child_session_client.close()

        open_app_tool.close()

        if audit is not None:
            audit.close()

        store.close()


if __name__ == "__main__":
    asyncio.run(main())
