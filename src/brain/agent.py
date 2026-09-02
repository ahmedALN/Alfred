from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.ai.providers.base import ChatProvider
from src.brain.diagnose import diagnose, supported
from src.brain.policy import Policy
from src.brain.skills import align, apply_params
from src.brain.types import Proposal, ProposalKind, Verdict
from src.tools.registry import ToolRegistry
from src.tools.results import summarize_result, tool_succeeded
from src.ui.live import LIVE

_PLAN_SYSTEM = """You are Alfred's task planner on a Windows PC. Turn the goal \
into the SHORTEST ordered plan a tool-using agent can execute.

Rules:
- 2 to 5 steps. Each step is ONE coherent operation with ONE outcome to \
check: open an app, run one search, run one PowerShell command. NOT a single \
keystroke or click ("focus the box" is not a step), and NOT several different \
operations crammed together ("create the folder, move the files, AND delete \
the zip" is three steps, not one).
- Do NOT add steps that only check, confirm, verify, or "get" a value - \
verification happens automatically after every step. The last step is the \
last real action, not "confirm it worked". Never pair a "get X" with a "then \
write X somewhere" - make the write its own step; the executor reads X from \
the log.
- "search Spotify and start the top track" is ONE step (one outcome: a track \
playing). "move the PDFs" and "delete the zip" are TWO steps (two outcomes).
- Every 'done_when' must be checkable from a tool result - a value a tool \
returns, a file that exists, text in a control. Never "it worked".
- To sleep, lock, restart, shut down or sign out, the step is just "Put the PC \
to sleep" (or lock / restart / shut down / sign out). There is a power TOOL for \
this. NEVER plan a PowerShell command for it: the machine stops being available \
before the command returns, so the shell route reports nothing back and looks \
exactly like having done nothing.
- powershell, system_info, network_info are TOOLS the executor calls directly. \
NEVER plan "Open PowerShell" or "Open a terminal" - to run a command the step \
is just "Run PowerShell to <do X>". Only plan "Open <app>" for a real GUI app \
the user will see (Spotify, a browser, Notepad, Settings).
- For work INSIDE an app, one step per thing the user would call a thing: \
"Open the app", "Search for X and open it", "Change setting Y to Z". The \
executor handles the clicks and typing within a step - do not plan those.
- Write steps in PLAIN ENGLISH, never as tool syntax. "Select all the text" \
is a step; "ui_control key keys='^a'" is NOT - the executor chooses the tool \
call, you describe the outcome.
- "Learn HOW TO x", "remember HOW TO x", "always do x this way" means BUILD A \
ROUTINE, not go and read about x. It is ONE step - "Learn a routine for x" - \
and the skill tool does it. Do NOT plan web searches or research: the user is \
asking Alfred to acquire a capability, not to look something up.
- That rule is about the VERB, never the words that follow it. "Open X", \
"run X", "launch X", "play X" is opening a thing that EXISTS ON THIS PC, \
even when the thing is named "How To Fish" or "How to draw". A name is not \
an instruction: only learn/remember/always makes a routine. "Open how to \
fish" is ONE step - "Open How to Fish" - and open_app finds it.
- "Remember THAT x" is a FACT to keep, not a routine and not a search. ONE \
step, the remember tool. "Remember that I hate coriander" is not a sequence \
of actions, so it can never be a routine.
- If the goal needs signing in, make that its own step ("Get to the sign-in \
screen"). Alfred never types passwords; the user does that part.
- For a "tell me / show me / what is / how much" question, the plan is just \
the query - ONE step, maybe two. Do NOT save the answer to a file or open \
Notepad unless the user explicitly asked for a file. The user gets the answer \
from the tool result.
- Prefer ui_control for apps, powershell / system_info / network_info for the \
machine's state. Use the ENVIRONMENT paths - never invent a username.
- ON SCREEN NOW says what is already open and what is in front. Plan from \
where the machine actually IS. If the app is already open there is no "open \
it" step - the plan starts at the work. Only plan opening what is genuinely \
not there.

Example goal: "play a Drake song on Spotify, then tell me what's playing"
{"plan":[
 {"step":"Open Spotify","done_when":"open_app returns success"},
 {"step":"Search Spotify for Drake and start the top track","done_when":"ui_control get on the now-playing text shows a Drake track"}],
 "note":""}
(Note: no third "tell me / confirm" step - that check is automatic.)

Example goal: "type hello into Notepad", with Notepad already in front
{"plan":[{"step":"Type hello into Notepad","done_when":"ui_control get on the editor shows hello"}],"note":""}
(One step. Notepad is open; opening it again is not a step.)

Example goal: "how much free space is on C"
{"plan":[{"step":"Run system_info to get disk space","done_when":"system_info returns a FreeGB value for C:"}],"note":""}
(One step. No "open PowerShell", no saving to a file.)

Reply with ONLY this JSON:
{"plan":[{"step":"<meaningful action>","done_when":"<checkable condition>"}],
 "note":"<one line about anything risky or uncertain, or empty>"}
"""

_EXEC_SYSTEM = """You are Alfred's task executor. Work ONLY on the CURRENT step \
using tools, one JSON object per reply, nothing else.

Use tools EXACTLY as the catalogue shows - the args in [brackets] are the only \
valid values for that parameter. Do not invent parameters.

Working INSIDE an app with ui_control:
 1. {"action":"wait_ready","window":"<app>"}   - right after launching it
 2. {"action":"tree","window":"<app>"}          - once, to see real controls
    (add "contains":"<word>" to find a control in a busy app)
 3. {"action":"type","window":"<app>","text":"<text>","into":<ref>}
 4. {"action":"click","window":"<app>","name":"<button>"} (or "ref":<n>)
 5. {"action":"get","window":"<app>","name":"<status text>"} - to confirm
 6. {"action":"close","window":"<app>"}         - to close a window
    (if it has unsaved work it comes back needs_user with the buttons -
     click "Don't save" only if the user asked to discard)
Also available: find, select (combo/list), expand, scroll, menu, key,
double_click, right_click, wait_for, windows, focus.

If APP NOTES below name a control, use that name directly - skip the
exploratory 'tree'. If the name turns out to be gone, THEN read the tree.

Rules:
- THE USER HAS SINCE SAID, if present, is the person speaking WHILE this job \
runs. It came after the plan was made, so where the two disagree the person \
wins. Change what you are doing to match. If their words make the current step \
pointless, say so with give_up and the reason - the plan is redone around what \
they said, and that is the right outcome, not a failure.
- Do the smallest set of actions that makes 'done_when' true, then action=done \
with the tool result that proves it.
- NEVER repeat a call with the same args - HISTORY shows what you already did.
- ON SCREEN NOW lists what is already open and which window is in front. If the app you need is there, do NOT open it and do NOT wait_ready for it - go straight to the work. Only open what is genuinely missing.
- After 2-3 failed calls, change tool or approach; if truly stuck, action=give_up.
- Do not claim done unless a real tool result in HISTORY shows done_when holds.
- NEVER type a password, PIN or security code. If the step needs a sign-in, \
get to the sign-in screen, then action=give_up with reason \
"waiting for the user to sign in" - Alfred will ask them.

Reply with exactly one of:
{"action":"use_tool","tool":"<name>","args":{...},"rationale":"<short>"}
{"action":"done","evidence":"<quote the tool result that proves done_when>"}
{"action":"give_up","reason":"<what blocked you>"}
"""

_VERIFY_SYSTEM = """You judge whether one task step is complete, from its tool \
log. Be fair, not pedantic:
- A tool result in the log that shows the intended action succeeded (a success \
status for the right action, the expected file/'control/text present) IS \
evidence - say VERIFIED.
- Say UNVERIFIED only if the log shows the action failed, was never attempted, \
or clearly shows the opposite of 'done_when'.
- The executor merely saying "done" is not evidence on its own.

Reply with exactly one line: 'VERIFIED: <which log line shows it>' or \
'UNVERIFIED: <what is missing or failed>'."""

_THINKING_OFF = (
    "detailed thinking off. Output only what the user's message asks for - "
    "no analysis, no preamble, no explanation."
)

_REFLECT_SYSTEM = """You are Alfred reviewing a task you just finished, to get \
better next time.

If a step failed or was slow for a concrete, reusable reason (a wrong UI \
assumption, a better tool for the job, a path that doesn't exist), state that \
lesson on one line starting 'LESSON: ' - phrased as a durable fact, not a \
description of this one run.

If there is no useful lesson, reply with exactly 'none'."""


def _opened_already(app: str, line: str) -> bool:
    """Does this history line show THIS app being opened successfully?

    It used to ask whether the app's name appeared anywhere in the
    line - results included. A successful launch reports where it
    launched from, so a game under D:\\SteamLibrary\\steamapps\\ was
    proof that "Steam" was already open, and Alfred quietly declined
    to start the launcher the game was waiting for. Every app whose
    name turns up inside a path or a message had the same problem.

    Only the call's own arguments say what was opened, and only a
    whole word matches: "steam" is not "steamapps".
    """
    if "open_app(" not in line or "-> ok:" not in line:
        return False
    call = line.split("-> ", 1)[0]
    return re.search(rf"\b{re.escape(app)}\b", call, re.I) is not None


@dataclass
class Step:
    index: int
    thought: str
    tool: str | None
    args: dict[str, Any]
    verdict: str
    result: Any
    ok: bool


@dataclass
class TaskResult:
    goal: str
    status: str  # done | partial | failed | uncertain | gave_up | exhausted
                 # | cancelled | error
    summary: str
    steps: list[Step] = field(default_factory=list)
    skipped_confirmations: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    # What the work found, as opposed to what it did. Empty when
    # there was nothing to find out.
    answer: str = ""
    # The learned routine this run replayed, if it replayed one. Not
    # reported; it is how the goal-relevance check knows the request
    # was already matched against the routine by name and by meaning.
    replayed_template: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "status": self.status,
            "summary": self.summary,
            "steps": len(self.steps),
            "plan": self.plan,
            "verified": self.verified,
            "unverified": self.unverified,
            "skipped_confirmations": self.skipped_confirmations,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
        }

    def tool_trace(self) -> list[tuple[str, dict[str, Any]]]:
        """The tool calls that actually ran and succeeded, in order -
        the raw material for distilling a reusable skill."""
        return [
            (s.tool, s.args)
            for s in self.steps
            if s.ok and s.verdict == "auto" and s.tool
        ]


# Below this many steps, a job finishes before a running commentary
# would have been any use, and the commentary is just interruption.
_WORTH_NARRATING = 4



# Words a tool uses when it is saying the goal is not reachable, as
# opposed to reporting that one attempt went wrong.
_REFUSAL_WORDS = (
    "cannot do", "can not do", "cannot be done", "is not possible",
    "not possible", "physical task", "refused", "i can only",
    "is not something", "unable to do", "there is no tool",
)


def _refused_in(steps: list[Step]) -> str:
    """A refusal among these steps, if one stands unanswered.

    Reads the step objects rather than the log text. The first version
    of this parsed log lines and asked whether the same TOOL later
    succeeded - so `skill list` working counted as answering `skill
    learn` being refused, which is the very mistake it was written to
    catch. Tool and action both have to match.
    """
    def action_of(step: Step) -> str:
        args = step.args if isinstance(step.args, dict) else {}
        return str(args.get("action") or args.get("query") or "")

    for i, step in enumerate(steps):
        if step.ok:
            continue
        text = f"{step.result}".lower()
        if not any(word in text for word in _REFUSAL_WORDS):
            continue

        # Refused, then made to work by the same call? Then it was not
        # a refusal of the goal.
        later = steps[i + 1:]
        answered = any(
            s.ok and s.tool == step.tool and action_of(s) == action_of(step)
            for s in later
        )
        if answered:
            continue

        detail = f"{step.tool}"
        if action_of(step):
            detail += f" {action_of(step)}"
        return f"{detail}: {text[:150]}"
    return ""


class TaskAgent:
    """
    Bounded plan -> act -> observe -> retry loop.

    Runs synchronously (call it from a worker thread). Executes tool
    calls through the shared ToolRegistry, gated by the same Policy the
    background brain uses: safe/reversible steps run, dangerous ones are
    skipped and reported for the user to approve later.
    """

    def __init__(
        self,
        chat: ChatProvider,
        registry: ToolRegistry,
        policy: Policy,
        *,
        policy_voice: Policy | None = None,
        plan_chat: ChatProvider | None = None,
        fast_chat: ChatProvider | None = None,
        vision: Any = None,
        screenshot: Any = None,
        undo: Any = None,
        verify_chat: ChatProvider | None = None,
        max_steps: int = 16,
        max_seconds: float = 240.0,
        substep_max_calls: int = 5,
        situation: Callable[[], str] | None = None,
        learner: Any = None,
        app_memory: Any = None,
        audit: Any = None,
        limitations: Any = None,
    ) -> None:
        self._chat = chat
        # Walls hit before, and what got past them.
        self._limitations = limitations
        self._last_wall: str = ""
        self._wall_tool: str = ""
        self._plan_chat = plan_chat or chat
        # Reading an answer out of tool output and writing a one-line
        # lesson are small, well-specified jobs. Sending them to the
        # planner made a learned routine take eleven seconds, ten of
        # them summarising one line of PowerShell output.
        self._fast_chat = fast_chat or self._plan_chat
        # For checking with its eyes that a thing actually
        # happened, rather than only that it was attempted.
        self._vision = vision
        self._screenshot = screenshot
        # A short memory of things that could be put back, written
        # as they are done rather than reconstructed afterwards.
        self._undo = undo
        # Verification defaults to the FAST model: the deterministic
        # fast-paths + strict per-substep scoping carry most of the load,
        # and a strong-model verify on every step of a multi-step task
        # roughly doubles latency. Pass verify_chat= to override.
        self._verify_chat = verify_chat or chat
        self._registry = registry
        self._situation = situation
        self._learner = learner
        self._app_memory = app_memory
        self._policy_brain = policy
        self._policy_voice = policy_voice or policy
        self._policy = policy  # active policy, set per run()
        self._ask_user: Callable[[str], bool] | None = None
        self._max_steps = max_steps
        self._max_seconds = max_seconds
        self._substep_max_calls = substep_max_calls
        self._audit = audit
        self._catalogue = ""
        self._exec_catalogue = ""
        self._plan_gripe = ""
        self._run_knowledge = ""
        self._run_apps = ""
        self._planned_ever: set[str] = set()
        self._first_plan_len = 0
        self._deadline = 0.0
        self._cancel_check: Callable[[], bool] = lambda: False

    # ----------------------------------------------------------------

    @staticmethod
    def _environment() -> str:
        home = os.path.expanduser("~")
        return (
            f"Windows. User profile: {home}. "
            f"Downloads: {os.path.join(home, 'Downloads')}. "
            f"Documents: {os.path.join(home, 'Documents')}. "
            f"Desktop: {os.path.join(home, 'Desktop')}."
        )

    def _tool_catalogue(self, *, full: bool) -> str:
        """One catalogue builder. Always surfaces each tool's enum values
        (e.g. ui_control action: tree|click|type|get) - the single most
        useful hint for a weak executor model - and, in full mode, the
        complete description for the planner."""
        lines = []
        for t in self._registry.gemini_declarations():
            name = t.get("name")
            desc = (t.get("description", "") or "").strip()
            if not full:
                desc = desc[:300]
            enums = _enum_hints(t.get("parameters", {}))
            suffix = f"  [{enums}]" if enums else ""
            lines.append(f"- {name}: {desc}{suffix}")
        return "\n".join(lines)

    # ----------------------------------------------------------------

    def run(
        self,
        goal: str,
        session_id: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        on_progress: Callable[[str], None] | None = None,
        *,
        source: str = "brain",
        ask_user: Callable[[str], bool] | None = None,
        steers: Callable[[], list[str]] | None = None,
    ) -> TaskResult:
        goal = goal.strip()
        started = time.monotonic()
        self._deadline = started + self._max_seconds
        self._cancel_check = cancel_check or (lambda: False)
        self._steers = steers or (lambda: [])
        # What the user has said since this job started. Kept for the
        # whole task, not just the step it arrived during: "not that
        # one, the other one" has to still be true three steps later.
        self._said_since: list[str] = []

        # Whether a person asked, not which door they used. A job typed
        # into WhatsApp is every bit as much a request as one spoken
        # aloud; judging it by the brain's rules - which exist for
        # things Alfred decided to do unprompted - made ordinary steps
        # need a confirmation nobody could give, so they were skipped
        # in silence. That is the whole of "he said he was doing it and
        # didn't do it".
        self._policy = (
            self._policy_brain if source == "brain" else self._policy_voice
        )
        self._ask_user = ask_user

        self._goal_now = goal
        result = TaskResult(goal=goal, status="failed", summary="")
        self._catalogue = self._tool_catalogue(full=True)
        self._exec_catalogue = self._tool_catalogue(full=False)
        self._planned_ever: set[str] = set()
        history: list[str] = []
        self._log("task_start", {"goal": goal}, session_id)

        if self._cancel_check():
            result.status = "cancelled"
            result.elapsed_seconds = time.monotonic() - started
            self._finalize(result)
            return result

        # Retrieve goal-relevant know-how once (embedding call); reused by
        # planning and every substep.
        self._run_knowledge = self._relevant_knowledge(goal)
        self._run_apps = self._app_profiles(goal)

        # 1. PLAN
        #
        # Tried skipping this for obviously-single-action goals - "open
        # Notepad" costing a model call to be told it is one step looked
        # like pure waste. Measured, it was worse: 4 model calls instead
        # of 3. The planner is not just producing a step, it is producing
        # a CHECKABLE done_when, and without one the verifier cannot
        # settle the step deterministically and has to ask a model, while
        # the executor takes more turns for want of a precise target.
        # The call pays for itself twice over. Left alone deliberately.
        plan = self._make_plan(goal)
        result.plan = [p["step"] for p in plan]
        self._first_plan_len = len(plan)
        self._planned_ever.update(result.plan)
        # Reading the plan out is worth it only when the job is long
        # enough that silence would be worrying. For "open Steam" it is
        # noise, and it arrives before anything has happened, which is
        # the least useful moment to be talked at.
        if on_progress is not None and len(plan) >= _WORTH_NARRATING:
            on_progress(
                f"This one's {len(plan)} steps: "
                + "; ".join(result.plan[:6])
            )
        self._log("task_plan", {"goal": goal, "plan": plan}, session_id)

        replans = 0
        pi = 0
        total_calls = 0
        dead_streak = 0  # consecutive steps that made zero progress
        heard_at_plan = len(self._said_since)
        steer_replans = 0
        # Steps whose failure was answered by doing one thing first,
        # and the lesson that is worth keeping if it turns out to work.
        repairs = 0
        repaired: set[str] = set()
        pending_fix: dict[str, Any] = {}
        # Set when looking at the world contradicted a tool result, and
        # cleared by the next action that actually works. While it is
        # set, "an earlier step probably already covered this" is not
        # available: the world was checked and the thing was not there,
        # and nothing has happened since to change that.
        world_refuted = False

        while pi < len(plan):
            if self._cancel_check():
                result.status = "cancelled"
                break

            # Someone spoke while this was running. Until now that only
            # reached a replan if a step FAILED - so a job that was
            # working fine ran to the end answering the question the
            # person had already moved on from. "open how to fish" ->
            # "on my desktop" still returned a fishing article, because
            # the search step succeeded and nothing reconsidered it.
            self._heard()
            if len(self._said_since) > heard_at_plan and steer_replans < 2:
                steer_replans += 1
                heard_at_plan = len(self._said_since)
                said = chr(10).join(self._said_since[-3:])
                self._log(
                    "task_steered",
                    {"goal": goal, "said": said, "was_on_step": pi},
                    session_id,
                )
                remainder = self._make_plan(goal, extra=(
                    f"Done so far: {result.verified or 'nothing'}. "
                    f"THE USER HAS SINCE SAID: {said} "
                    "They said it while this was running, so it comes "
                    "AFTER the plan and overrides it. Re-plan what is "
                    "left around what they said - if the remaining "
                    "steps answer the wrong question now, drop them."))
                if remainder:
                    plan = remainder
                    pi = 0
                    dead_streak = 0
                    result.plan = [st["step"] for st in plan]
                    self._log(
                        "task_plan", {"goal": goal, "plan": plan}, session_id
                    )
                    continue
            if time.monotonic() > self._deadline:
                result.status = "exhausted"
                break
            if total_calls >= self._max_steps:
                break
            # Two dead steps with nothing EVER having worked is a task
            # that never got off the ground - a wrong app name, a
            # missing permission, a plan built on a false assumption.
            # Grinding out a third costs a minute and a model call to
            # learn what the first two already said.
            #
            # Three is right when earlier steps DID work: that is one
            # hard step in a job that is otherwise going fine, and
            # giving up early there throws away real progress.
            limit = 3 if result.verified else 2

            if dead_streak >= limit:
                # Tried and got nowhere - grinding the rest of the
                # budget won't help. Record the rest as unverified.
                for p in plan[pi:]:
                    result.unverified.append(f"{p['step']} (gave up - no progress)")
                break

            pstep = plan[pi]
            if (
                on_progress is not None
                and pi > 0
                and len(plan) >= _WORTH_NARRATING
            ):
                on_progress(f"Step {pi + 1}/{len(plan)}: {pstep['step'][:70]}")

            before = len(result.steps)
            hist_before = len(history)
            calls = self._execute_substep(
                goal, plan, pi, history, result, session_id,
                budget=self._max_steps - total_calls,
            )
            total_calls += calls
            sub_hist = history[hist_before:]
            sub_steps = result.steps[before:]
            progressed_here = any(s.ok for s in sub_steps)

            if progressed_here:
                ok, evidence = self._verify(pstep, history, sub_hist, sub_steps)
            elif not calls and not world_refuted and any(
                "-> ok:" in h for h in history[:hist_before]
            ):
                # Zero tool calls this step but earlier work exists - could
                # be "already done" after a replan. Let the verifier judge
                # from the whole log, but it must find real evidence.
                ok, evidence = self._verify(pstep, history, sub_hist, sub_steps)
            else:
                ok, evidence = False, "no successful tool action for this step"
            # The verifier reads the log, and the log records what the
            # tools SAID. Where there is a fact to be had instead - the
            # app is running, the file is on disk - go and look at it.
            if ok:
                trouble = self._aftercheck(sub_steps)
                if trouble is not None:
                    ok = False
                    world_refuted = True
                    evidence = str(trouble)
                    # The line that said it worked is now known to be
                    # wrong. Correct the record, or the already-open
                    # guard reads it as proof the app is up and refuses
                    # to try again - which is the one thing that has to
                    # happen next.
                    for i in range(len(history) - 1, -1, -1):
                        if "-> ok:" in history[i]:
                            history[i] = history[i].replace(
                                "-> ok:", "-> ok, but it did not last:", 1
                            )
                            break
                    history.append(f"[looked] {evidence}")
                    self._log(
                        "task_aftercheck",
                        {"step": pstep["step"], "found": evidence},
                        session_id,
                    )

            self._log(
                "task_verify",
                {"step": pstep["step"], "verified": ok, "evidence": evidence},
                session_id,
            )

            if ok:
                result.verified.append(pstep["step"])
                dead_streak = 0
                world_refuted = False
                # It worked, and it worked because of something done
                # first. That is worth knowing next time.
                fixed_by = pending_fix.pop(pstep["step"], None)
                if fixed_by is not None and fixed_by.lesson:
                    self._keep_lesson(fixed_by.lesson, session_id)
                pi += 1
                continue

            # Most failures do not want a different plan, they want one
            # thing doing first. Try that before rewriting everything:
            # it is cheaper, it is often certain, and a replan that
            # drops the goal is how "open the game" became a web search.
            if repairs < 2 and pstep["step"] not in repaired:
                fix = self._repair_for(sub_steps, evidence)
                if fix is not None:
                    repairs += 1
                    repaired.add(pstep["step"])
                    pending_fix[pstep["step"]] = fix
                    history.append(f"[first] {fix.step} - {fix.why}")
                    self._log(
                        "task_repair",
                        {"step": pstep["step"], "first": fix.step,
                         "why": fix.why, "certain": fix.certain},
                        session_id,
                    )
                    plan = (
                        [*plan[:pi], {"step": fix.step, "done_when": f"{fix.tool} returns success"}, *plan[pi:]]
                    )
                    result.plan = [p["step"] for p in plan]
                    self._planned_ever.update(result.plan)
                    continue

            # Count toward the give-up streak only when the step genuinely
            # tried (made tool calls) and still got nowhere - not when the
            # executor just declared done without acting.
            tried_and_failed = calls > 0 and not progressed_here
            dead_streak = dead_streak + 1 if tried_and_failed else dead_streak

            if replans < 2 and total_calls < self._max_steps:
                replans += 1
                # Read what actually went wrong before asking for a
                # different plan. "Not verified" is a fact about the
                # verifier, not about the world; without a cause the
                # planner picks its next move by luck.
                finding = self._diagnose_last(result, goal)
                if finding:
                    history.append(f"[why] {finding}")
                history.append(
                    f"[replan {replans}] step '{pstep['step']}' not verified: "
                    f"{evidence}"
                )
                # A replan is exactly where a mid-task correction has to
                # land: the old plan was made before the person spoke,
                # and the rest of it may be answering the wrong question
                # entirely.
                said = self._heard()
                remainder = self._make_plan(
                    goal,
                    extra=(
                        f"Done so far: {result.verified or 'nothing'}. "
                        f"Stuck on: {pstep['step']} - {evidence}. "
                        + (f"WHY IT FAILED: {finding} " if finding else "")
                        + (
                            "The user has SINCE SAID:\n" + said
                            + "\nPlan the rest around that - it "
                            "overrides the original goal where they "
                            "disagree.\n"
                            if said else ""
                        )
                        + "Give the remaining steps only."
                    ),
                )
                plan = plan[:pi] + remainder
                result.plan = [p["step"] for p in plan]
                self._planned_ever.update(result.plan)
                continue

            result.unverified.append(f"{pstep['step']} ({evidence})")
            pi += 1

        # 4. REPORT - only from what was verified
        result.elapsed_seconds = time.monotonic() - started
        self._finalize(result)
        self._log("task_end", result.as_dict(), session_id)
        return result

    # ----------------------------------------------------------------

    def replay(
        self,
        skill: dict[str, Any],
        request: str,
        session_id: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        on_progress: Callable[[str], None] | None = None,
        *,
        source: str = "voice",
        ask_user: Callable[[str], bool] | None = None,
        steers: Callable[[], list[str]] | None = None,
    ) -> TaskResult:
        """Run a learned skill's steps directly - no planning call. Params
        are filled from ``request``; the skill's ``verify`` is still checked
        so a stale skill can't lie."""

        goal = request.strip()
        started = time.monotonic()
        self._deadline = started + self._max_seconds
        self._cancel_check = cancel_check or (lambda: False)
        self._steers = steers or (lambda: [])
        self._said_since = []
        self._policy = (
            self._policy_voice if source == "voice" else self._policy_brain
        )
        self._ask_user = ask_user

        self._first_plan_len = 0  # replay has no plan to shrink
        done_when = str(skill.get("verify") or goal)
        result = TaskResult(goal=goal, status="failed", summary="")
        result.plan = [done_when]
        # A skill's `verify` is deliberately written in different words
        # from the request - "a track is playing" for "play adele on
        # spotify". The routine's own name is what ties the run to what
        # was asked.
        result.replayed_template = str(skill.get("template") or "")

        values = align(str(skill.get("template", "")), goal) or {}
        missing = [p for p in skill.get("params", []) if p not in values]
        if missing:
            result.unverified.append(
                f"{done_when} (couldn't read {', '.join(missing)} from '{goal}')"
            )
            result.elapsed_seconds = time.monotonic() - started
            self._finalize(result)
            self._log("skill_replay", result.as_dict(), session_id)
            return result

        steps = apply_params(list(skill.get("steps", [])), values)
        history: list[str] = []
        if on_progress is not None:
            on_progress(f"Doing '{goal}' from a saved routine.")

        for i, st in enumerate(steps, 1):
            if self._cancel_check():
                result.status = "cancelled"
                break
            if time.monotonic() > self._deadline:
                result.status = "exhausted"
                break
            decision = {
                "tool": st.get("tool"),
                "args": st.get("args", {}),
                "rationale": f"replay step {i} of '{skill.get('name', 'skill')}'",
            }
            step = self._run_tool_step(
                len(result.steps) + 1, decision, history, result, session_id
            )
            result.steps.append(step)
            self._note_wall(step)

        progressed = any(s.ok for s in result.steps)

        if result.status not in ("cancelled", "exhausted"):
            if not progressed:
                result.unverified.append(f"{done_when} (skill ran no actions)")
            else:
                ok, evidence = self._verify(
                    {"step": skill.get("name", goal), "done_when": done_when},
                    history,
                )
                if ok:
                    result.verified.append(done_when)
                else:
                    result.unverified.append(f"{done_when} ({evidence})")

        result.elapsed_seconds = time.monotonic() - started
        self._finalize(result)
        self._log("skill_replay", result.as_dict(), session_id)
        return result

    # ----------------------------------------------------------------

    def _make_plan(self, goal: str, extra: str = "") -> list[dict[str, str]]:
        know = getattr(self, "_run_knowledge", "") or self._relevant_knowledge(goal)
        apps = getattr(self, "_run_apps", "")
        seen = self._onscreen()
        base_prompt = (
            f"{_PLAN_SYSTEM}\n\nGOAL: {goal}\n\n"
            f"ENVIRONMENT: {self._environment()}\n\n"
            + (f"APP NOTES (what worked here before):\n{apps}\n\n" if apps else "")
            + (f"KNOWN GOOD PRACTICE:\n{know}\n\n" if know else "")
            + (f"SITUATION:\n{self._situation_text()}\n\n" if self._situation else "")
            + (f"ON SCREEN NOW:\n{seen}\n\n" if seen else "")
            + f"TOOLS:\n{self._catalogue}\n"
            + (f"\nCONTEXT: {extra}\n" if extra else "")
        )

        steps: list[dict[str, str]] = []
        for attempt in range(2):
            prompt = base_prompt + (
                "\nYour JSON:" if attempt == 0 else
                f"\nThat plan was rejected ({self._plan_gripe}). "
                "Give a better one - real imperative sentences, checkable "
                "done_when, 2 to 6 steps.\nYour JSON:"
            )
            try:
                raw = self._plan_chat.generate(
                    prompt, system=_THINKING_OFF,
                    temperature=0.2, max_tokens=2000,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[Task] planner failed ({exc}); using a single step.")
                return [{"step": goal, "done_when": goal}]

            obj = _parse(raw) or {}
            raw_plan = obj.get("plan")
            steps = []
            if isinstance(raw_plan, list):
                for item in raw_plan:
                    if isinstance(item, dict) and item.get("step"):
                        s = str(item["step"]).strip()
                        dw = str(item.get("done_when", "")).strip() or s
                        steps.append({"step": s, "done_when": dw})

            if self._plan_ok(steps, goal):
                return steps[:8]

        # Both attempts weak - use whatever we got, or a single step.
        return steps[:8] or [{"step": goal, "done_when": goal}]

    def _plan_ok(self, steps: list[dict[str, str]], goal: str) -> bool:
        self._plan_gripe = ""
        if not steps:
            self._plan_gripe = "empty plan"
            return False
        if len(steps) > 8:
            self._plan_gripe = "too many steps"
            return False
        norm = [" ".join(s["step"].lower().split()) for s in steps]
        if len(set(norm)) < len(norm):
            self._plan_gripe = "the plan repeats a step - each step must be distinct"
            return False
        # near-duplicates ("check free space" / "get free space information")
        kw = [
            {w for w in n.split() if len(w) > 3 and w not in self._VERIFY_STOP}
            for n in norm
        ]
        for i in range(len(kw)):
            for j in range(i + 1, len(kw)):
                a, b = kw[i], kw[j]
                if a and b and len(a & b) / len(a | b) >= 0.6:
                    self._plan_gripe = (
                        f"steps {i + 1} and {j + 1} are near-duplicates - merge them"
                    )
                    return False
        # explicit verify/confirm-only steps (verification is automatic)
        for s in steps:
            t = s["step"].lower()
            if (
                t.startswith(("verify ", "confirm ", "check that ", "ensure that "))
                or t.startswith(("get the current", "get the currently"))
            ) and s is not steps[0]:
                self._plan_gripe = (
                    f"drop the check-only step {s['step']!r} - verification is "
                    "automatic"
                )
                return False
        for s in steps:
            text = s["step"]
            # identifier-like ("search_spotify_top_track") or one bare word
            if " " not in text and (
                "_" in text or (text.isalnum() and len(text) > 12)
            ):
                self._plan_gripe = f"step is not a real sentence: {text!r}"
                return False
            # a tool call pasted in as a step ("ui_control key keys='^a'")
            if _TOOL_SYNTAX.search(text):
                self._plan_gripe = (
                    f"step {text!r} is tool syntax - describe the outcome in "
                    "plain English and let the executor pick the tool"
                )
                return False
            # a UI micro-action the executor handles on its own
            if _MICRO_ACTION.search(text):
                self._plan_gripe = (
                    f"step {text!r} is a UI micro-action - the executor "
                    "finds and focuses controls itself; plan the outcome"
                )
                return False
            dw = s["done_when"].strip()
            if not dw or dw.lower() == text.lower():
                self._plan_gripe = f"step has no checkable done_when: {text!r}"
                return False

        # The goal asks to change something, but every step only looks.
        gl = goal.lower()
        if any(v in gl for v in _MUTATION_INTENT):
            plan_text = " ".join(s["step"].lower() for s in steps)
            if not any(v in plan_text for v in _MUTATION_VERBS):
                self._plan_gripe = (
                    "the goal asks to change something but the plan only "
                    "inspects - add the step(s) that actually do it"
                )
                return False
        return True

    def _note_wall(self, step: Step) -> None:
        """Count what Alfred ran into, and what got past it.

        A failure followed by a different tool succeeding is the only
        evidence of a workaround worth having - it is a route that
        actually worked, rather than a plausible-sounding guess about
        one.
        """
        if self._limitations is None:
            return

        args = step.args or {}
        app = str(
            args.get("window") or args.get("app") or args.get("name") or ""
        )[:40]

        try:
            if not step.ok:
                self._last_wall = self._limitations.hit(
                    step.tool, _why_it_failed(step), app
                )
                self._wall_tool = step.tool
                return

            # Only the same tool succeeding counts. Alfred failed a
            # PowerShell command, went and looked at the screen, and
            # recorded "when powershell fails, use desktop_control look"
            # as a standing lesson - which is not a workaround, it is
            # just whatever happened next. A wrong lesson is worse than
            # no lesson, because it gets followed.
            if self._last_wall and step.tool == self._wall_tool:
                how = _short(args, 120)
                self._limitations.got_past(self._last_wall, how)
                self._last_wall = ""
        except Exception as exc:  # noqa: BLE001
            print(f"[Task] could not record the wall: {exc}")

    def learn_workarounds(self) -> list[str]:
        """Turn walls hit more than once into lasting knowledge.

        Only where both halves are present: it happened again, and
        something was seen to get past it. One failed run is bad luck,
        and a lesson written from bad luck is a fact that is not true.
        """
        if self._limitations is None or self._learner is None:
            return []

        learned: list[str] = []

        try:
            ready = self._limitations.ready_to_teach()
        except Exception:  # noqa: BLE001
            return []

        for wall in ready:
            detail = (wall.get("detail") or "").strip()
            where = f" in {wall['app']}" if wall.get("app") else ""
            lesson = (
                f"When {wall['tool']}{where} fails with "
                f"\"{detail[:90]}\", it is not a one-off - it has happened "
                f"{wall['hits']} times. What worked instead: "
                f"{wall['workaround']}."
            )

            try:
                self._learner.remember(
                    content=lesson,
                    category="correction",
                    source="learned_workaround",
                )
                if self._app_memory is not None and wall.get("app"):
                    self._app_memory.note(
                        wall["app"],
                        f"When {wall['tool']} fails here, "
                        f"{wall['workaround']} works.",
                        kind="workaround",
                    )
                self._limitations.mark_taught(wall["signature"])
                learned.append(lesson)
            except Exception as exc:  # noqa: BLE001
                print(f"[Task] could not store a workaround: {exc}")

        return learned

    def _diagnose_last(self, result: TaskResult, goal: str) -> str:
        """The most recent real failure, read rather than assumed."""
        for step in reversed(result.steps):
            if step.ok:
                continue
            try:
                finding = diagnose(
                    step.tool, step.args, step.result, goal,
                    chat=self._fast_chat,
                )
            except Exception:  # noqa: BLE001
                return ""
            return str(finding)
        return ""

    def reflect(self, result: TaskResult) -> str:
        """One cheap post-mortem call. Turns a concrete failure reason into
        a durable LESSON fact for next time. Returns the reflection line
        (or '') for logging. Safe to call from a worker thread."""
        if not result.steps:
            return ""

        # With the error text, not without it. Shown only "FAILED", the
        # post-mortem invented causes - "the skill tool does not support
        # list or learn actions" is false, and is one of sixty-nine such
        # lessons now sitting in memory stopping Alfred trying things.
        errors = [_why_it_failed(s) for s in result.steps if not s.ok]
        trace = "\n".join(
            f"- {s.tool}({_short(s.args, 80)}) -> "
            + ("ok" if s.ok else f"FAILED: {_why_it_failed(s)}")
            for s in result.steps
        )
        prompt = (
            f"{_REFLECT_SYSTEM}\n\nGOAL: {result.goal}\n"
            f"OUTCOME: {result.status}\n"
            f"VERIFIED: {result.verified or 'nothing'}\n"
            f"NOT VERIFIED: {result.unverified or 'n/a'}\n\n"
            f"STEPS:\n{trace}\n\nYour one line:"
        )
        try:
            line = self._fast_chat.generate(
                prompt, system=_THINKING_OFF, temperature=0.2, max_tokens=500
            ).strip()
        except Exception as exc:  # noqa: BLE001
            return f"(reflection failed: {exc})"

        line = line.splitlines()[0].strip() if line else ""
        if line.upper().startswith("LESSON:") and self._learner is not None:
            lesson = line.split(":", 1)[1].strip()
            if not supported(lesson, errors):
                # It concluded something the evidence does not say. A
                # wrong lesson is worse than none: it is permanent, and
                # it stops Alfred attempting that thing again.
                print(f"[Task] not storing an unsupported lesson: {lesson[:70]}")
                self._log("task_reflect",
                          {"goal": result.goal, "line": line,
                           "stored": False, "why": "unsupported"}, None)
                return line
            if len(lesson) > 8:
                try:
                    self._learner.remember(
                        content=lesson,
                        category="correction",
                        source="task_reflection",
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[Task] could not store lesson: {exc}")
        self._log("task_reflect", {"goal": result.goal, "line": line}, None)
        return line

    def _eyes_disagree(self, pstep: dict[str, str]) -> tuple[bool, str]:
        """Look at the screen and see whether it contradicts the claim."""
        if getattr(self, "_vision", None) is None:
            return False, ""
        if getattr(self, "_screenshot", None) is None:
            return False, ""

        from src.brain.looksright import contradicted, worth_looking

        done_when = pstep.get("done_when", "")
        if not worth_looking(done_when):
            return False, ""

        try:
            return contradicted(
                pstep.get("step", ""), done_when,
                self._screenshot, self._vision,
            )
        except Exception:  # noqa: BLE001
            return False, ""

    def _heard(self) -> str:
        """Anything said to this job since it started.

        Drained from the queue's mailbox and then kept, because a
        correction has to still be true several steps later. "Not
        that one, the other one" is not advice about one click.
        """
        try:
            fresh = self._steers() or []
        except Exception:  # noqa: BLE001
            fresh = []

        for note in fresh:
            note = str(note).strip()
            if note and note not in self._said_since:
                self._said_since.append(note)

        if not self._said_since:
            return ""
        return "\n".join(f'- \"{note}\"' for note in self._said_since[-6:])

    def _onscreen(self) -> str:
        """What any glance would show. About twenty milliseconds."""
        try:
            from src.brain.onscreen import look

            return look().brief()
        except Exception:  # noqa: BLE001
            return ""

    def _situation_text(self) -> str:
        if self._situation is None:
            return ""
        try:
            return (self._situation() or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[Task] situation probe failed: {exc}")
            return ""

    def _app_profiles(self, goal: str) -> str:
        """What Alfred already knows about the app(s) this goal names."""
        if self._app_memory is None:
            return ""
        try:
            return (self._app_memory.profiles_for(goal) or "").strip()
        except Exception as exc:  # noqa: BLE001
            print(f"[Task] app memory lookup failed: {exc}")
            return ""

    def _relevant_knowledge(self, goal: str, k: int = 5) -> str:
        """Pull the handful of learned facts / playbook entries most
        relevant to this goal, so planning starts from good practice."""
        if self._learner is None:
            return ""
        try:
            facts = self._learner.recall(goal, top_k=k)
            if not facts:
                # Nothing cleared the confidence bar. For a planning hint
                # a near miss still beats silence - "what have you been
                # doing today" scored 0.535 against a 0.55 threshold and
                # came back with nothing at all. The planner can ignore a
                # hint; it cannot use one it never saw.
                facts = self._learner.recall(goal, top_k=2, threshold=0.42)
        except Exception:  # noqa: BLE001
            return ""
        lines = []
        for f in facts:
            c = getattr(f, "content", str(f)).strip()
            if c and not c.upper().startswith(("SUPPRESS:", "GOAL:")):
                lines.append(f"- {c}")
        return "\n".join(lines[:k])

    def _execute_substep(
        self,
        goal: str,
        plan: list[dict[str, str]],
        pi: int,
        history: list[str],
        result: TaskResult,
        session_id: str | None,
        *,
        budget: int,
    ) -> int:
        pstep = plan[pi]
        plan_view = "\n".join(
            f"  {'>' if j == pi else ' '} {p['step']}"
            for j, p in enumerate(plan)
        )
        calls = 0
        seen_calls: dict[str, int] = {}
        tool_fails: dict[str, int] = {}
        loops = 0
        consecutive_fail = 0
        for _ in range(max(0, min(self._substep_max_calls, budget))):
            if self._cancel_check() or time.monotonic() > self._deadline:
                break

            know = getattr(self, "_run_knowledge", "")
            apps = getattr(self, "_run_apps", "")
            # Asked every time round rather than once per step: an app
            # opened on the first call was still "not open" for the
            # rest of them. The look is cached for a couple of seconds,
            # so asking again is free and is right more often.
            onscreen = self._onscreen()
            said = self._heard()
            prompt = (
                f"{_EXEC_SYSTEM}\n\nOVERALL GOAL: {goal}\n\nPLAN:\n{plan_view}\n\n"
                f"CURRENT STEP: {pstep['step']}\nDONE WHEN: {pstep['done_when']}\n\n"
                # Before the plan, the notes and the history, on purpose.
                # If the person has said something since this job
                # started, it beats everything decided before they said
                # it - including the plan.
                + (f"THE USER HAS SINCE SAID:\n{said}\n\n" if said else "")
                + f"ENVIRONMENT: {self._environment()}\n\n"
                # The executor was told the goal, the plan, the tools and
                # its own history, and nothing whatever about the machine
                # it was working on. So its opening move was to find out
                # what a glance would have shown it: launching apps that
                # were already running, waiting for windows already up.
                + (f"ON SCREEN NOW:\n{onscreen}\n\n" if onscreen else "")
                + (f"APP NOTES (controls that worked here before):\n{apps}\n\n"
                   if apps else "")
                + (f"KNOWN GOOD PRACTICE:\n{know}\n\n" if know else "")
                + f"TOOLS:\n{self._exec_catalogue}\n\n"
                + "HISTORY:\n" + ("\n".join(history[-16:]) or "(nothing yet)")
                + "\n\nYour next JSON:"
            )
            try:
                raw = self._chat.generate(
                    prompt, temperature=0.2, max_tokens=500
                )
            except Exception as exc:  # noqa: BLE001
                history.append(f"[system] executor model error: {exc}")
                break

            decision = _parse(raw)
            if decision is None:
                history.append("[system] unparseable model reply, retry")
                continue

            action = decision.get("action")
            if action == "done":
                history.append(
                    f"[step {pi + 1} executor claims done] "
                    f"{str(decision.get('evidence', ''))[:200]}"
                )
                break
            if action == "give_up":
                history.append(
                    f"[step {pi + 1} executor gave up] "
                    f"{str(decision.get('reason', ''))[:160]}"
                )
                break
            if action != "use_tool":
                history.append(f"[system] unknown action {action!r}")
                continue

            dtool = decision.get("tool")
            dargs = decision.get("args") if isinstance(
                decision.get("args"), dict
            ) else {}

            # The envelope's verb is called `action` and so is
            # ui_control's, and a model that has both in front of it
            # will sooner or later put one inside the other. It did:
            #
            #   {"action":"use_tool","tool":"ui_control",
            #    "args":{"action":"give_up","reason":"..."}}
            #
            # ui_control answered with its list of twenty-seven verbs,
            # the executor learned nothing, and a step that was
            # correctly being abandoned carried on. Read it as what it
            # plainly says.
            buried = str(dargs.get("action") or "").lower()

            if buried in ("give_up", "done"):
                why = str(
                    dargs.get("reason") or dargs.get("evidence") or ""
                )[:160]
                history.append(
                    f"[step {pi + 1} executor "
                    + ("claims done" if buried == "done" else "gave up")
                    + f"] {why}"
                )
                break

            # Already-open guard: re-launching an app that HISTORY shows
            # is open is the weak model's favourite wheel-spin.
            if dtool == "open_app":
                app = str(dargs.get("app") or dargs.get("name") or "").lower()
                if app and any(
                    _opened_already(app, h) for h in history
                ):
                    history.append(
                        f"[system] {app} is already open (see HISTORY) - "
                        "move on to the next action."
                    )
                    loops += 1
                    if loops >= 3:
                        break
                    continue

            # Loop guard: the weak local model loves to repeat a call.
            sig = f"{dtool}|{_short(dargs, 200)}"
            seen_calls[sig] = seen_calls.get(sig, 0) + 1
            if seen_calls[sig] >= 2:
                loops += 1
                history.append(
                    f"[system] you ALREADY ran {dtool} with those exact args "
                    "- its result is above. Do something different or reply "
                    "action=done / give_up."
                )
                if loops >= 3:
                    history.append(
                        f"[step {pi + 1} executor stuck in a loop] abandoned"
                    )
                    break
                continue

            step = self._run_tool_step(
                len(result.steps) + 1, decision, history, result, session_id
            )
            result.steps.append(step)
            self._note_wall(step)
            calls += 1

            if step.ok:
                consecutive_fail = 0
                tool_fails.pop(str(dtool), None)
            else:
                consecutive_fail += 1
                # Retrying one tool with slightly different arguments is
                # the weak model's other favourite wheel-spin: nudge it to
                # a different tool rather than a different spelling.
                key = str(dtool)
                tool_fails[key] = tool_fails.get(key, 0) + 1
                if tool_fails[key] == 2:
                    history.append(
                        f"[system] {key} has now failed twice - re-read its "
                        "parameters in TOOLS above, or use a DIFFERENT tool "
                        "to achieve this step."
                    )
                elif tool_fails[key] >= 3:
                    history.append(
                        f"[step {pi + 1} executor] {key} keeps failing - "
                        "abandoned this step"
                    )
                    break
                if consecutive_fail >= 3:
                    history.append(
                        f"[step {pi + 1} executor] 3 failed calls in a row - "
                        "abandoned this step"
                    )
                    break

        return calls

    _VERIFY_STOP = {  # noqa: RUF012
        "the", "a", "an", "is", "are", "was", "were", "and", "or", "to", "of",
        "in", "on", "at", "it", "that", "this", "with", "for", "shows", "show",
        "returns", "return", "tool", "result", "when", "done", "currently",
        "value", "text", "control", "window", "which", "line", "there",
    }

    def _aftercheck(self, sub_steps: list[Step]) -> Any:
        """The world's opinion of a step that has just claimed success.

        Only the most recent checkable action is looked at, and only a
        check with positive evidence AGAINST the claim counts. Silence
        means "nothing to check here", never "it failed".
        """
        from src.brain import aftercheck as _after

        for st in reversed(sub_steps):
            if not st.ok or not st.tool:
                continue
            found = _after.check(st.tool, st.args, st.result)
            if found is None:
                continue
            return None if found.ok else found
        return None

    def _repair_for(self, sub_steps: list[Step], evidence: str) -> Any:
        """One thing to do first that would make this step work."""
        from src.brain import repair as _repair

        for st in reversed(sub_steps):
            if st.tool != "open_app":
                continue
            res = st.result if isinstance(st.result, dict) else {}
            app = str(
                res.get("app") or st.args.get("app") or st.args.get("name") or ""
            )
            if not app:
                continue
            note = " ".join(
                x for x in (str(res.get("note") or ""),
                            str(res.get("error") or ""), evidence) if x
            )
            try:
                return _repair.prerequisite(
                    app,
                    executable=str(res.get("executable") or ""),
                    note=note,
                    chat=self._fast_chat,
                )
            except Exception:  # noqa: BLE001
                return None
        return None

    def _keep_lesson(self, lesson: str, session_id: str | None) -> None:
        if self._learner is None or not lesson:
            return
        try:
            self._learner.remember(
                lesson, category="how-to", source="task"
            )
            self._log("task_learned", {"lesson": lesson}, session_id)
        except Exception:  # noqa: BLE001
            pass

    def _verify(
        self,
        pstep: dict[str, str],
        history: list[str],
        sub_hist: list[str] | None = None,
        steps: list[Step] | None = None,
    ) -> tuple[bool, str]:
        done_when = pstep["done_when"]

        # Evidence for THIS step comes from THIS step's own log. If it did
        # nothing that worked here, only an earlier step that plausibly
        # already covered it (executor explicitly claims done) gets a
        # second look against the whole log.
        scope = sub_hist if sub_hist is not None else history
        if sub_hist is not None and not any("-> ok:" in h for h in sub_hist):
            claimed_done = any("executor claims done" in h for h in sub_hist)
            if not claimed_done:
                return False, "this step took no successful action"
            scope = history  # let the model check for prior coverage

        # A refusal settles it, and is not a judgement call.
        #
        # Asked to learn a routine for making a cup of tea, the skill
        # tool refused - correctly, saying it is a physical task these
        # tools cannot do. The executor then called `skill list`, which
        # succeeded, and the verifier accepted that as evidence. The
        # task reported "Confirmed: Learn a routine for making a cup of
        # tea". Alfred claimed to have learned something it had just
        # explained it could not.
        #
        # A tool saying "this cannot be done" is a statement about the
        # goal, not a transient failure, and no amount of other calls
        # succeeding afterwards changes it.
        refusal = _refused_in(steps or [])
        if refusal:
            return False, f"it was refused: {refusal}"

        # --- deterministic fast-paths (never say "no", only "yes") ------
        #
        # Always against THIS step's own log, even when the scope has
        # been widened for the model to look for prior coverage. The
        # overlap check counts signal words, and a done_when as ordinary
        # as "open_app returns success" is satisfied by ANY successful
        # open_app - so widening it let the launch of Steam stand as
        # proof that the game it was launched for had opened.
        narrow = sub_hist if sub_hist is not None else history
        probe = self._deterministic_verify(done_when, narrow)
        if probe is not None:
            return probe

        # --- model check ---------------------------------------------
        log_lines = scope[-18:]
        prompt = (
            f"{_VERIFY_SYSTEM}\n\nSTEP: {pstep['step']}\n"
            f"DONE WHEN: {done_when}\n\n"
            f"LOG:\n" + "\n".join(log_lines) + "\n\nYour one line:"
        )
        try:
            raw = self._verify_chat.generate(
                prompt, system=_THINKING_OFF, temperature=0.0, max_tokens=600
            ).strip()
        except Exception as exc:  # noqa: BLE001
            # Verifier unavailable: fall back to the fast model, then to a
            # lenient "a relevant tool call succeeded" heuristic.
            try:
                raw = self._chat.generate(
                    prompt, temperature=0.0, max_tokens=300
                ).strip()
            except Exception:  # noqa: BLE001
                ok = any("-> ok:" in h for h in history[-6:])
                return ok, "a relevant tool call succeeded" if ok else (
                    f"could not verify: {exc}"
                )

        # Find the verdict line anywhere (a reasoning model may preface it).
        verdict_line = ""
        for ln in raw.splitlines():
            u = ln.strip().upper()
            if u.startswith(("VERIFIED", "UNVERIFIED")):
                verdict_line = ln.strip()
                break
        if not verdict_line:
            verdict_line = raw.splitlines()[-1].strip() if raw else ""

        if verdict_line.upper().startswith("VERIFIED"):
            # A log saying a thing was attempted is not a screen showing
            # it happened. Consulted only when the log already said yes,
            # because the job here is catching a false success rather
            # than rescuing a real failure.
            denied, seen = self._eyes_disagree(pstep)
            if denied:
                return False, f"the screen says otherwise - {seen}"
            return True, verdict_line.split(":", 1)[-1].strip()[:200]
        return False, verdict_line.split(":", 1)[-1].strip()[:200] or "no evidence"

    def _deterministic_verify(
        self, done_when: str, history: list[str]
    ) -> tuple[bool, str] | None:
        """Yes, no, or don't know - checked against the world where it can be.

        Returns None for don't know, which sends the step to the model
        verifier. A definite no matters as much as a definite yes: it is
        what stops the lenient word-overlap check below agreeing with a
        step that did something else.
        """
        low = done_when.lower()

        # ---- is it open? go and look -------------------------------
        #
        # "Open research.txt from my Desktop" was reported done after
        # Alfred ran ui_control find, saw the filename in File
        # Explorer's listing, and never opened anything. The words
        # matched. The window was never there. Nothing had looked.
        if any(
            w in low for w in
            ("is open", "opens", "opened", "window", "visible", "showing",
             "on screen", "launched", "running", "in the foreground")
        ):
            wanted = _names_in(done_when)

            if wanted:
                titles = self._open_windows()

                # Only trust an empty result as "no" if the window list
                # itself worked - no windows at all means the reading
                # failed, not that the desktop is empty.
                if titles:
                    for name in wanted:
                        hit = [t for t in titles if name.lower() in t.lower()]

                        if hit:
                            return True, f"the window is there: {hit[0][:80]}"

                    return False, (
                        f"no window matching {', '.join(sorted(wanted))} - "
                        "nothing was opened"
                    )

        # ---- file / folder existence -------------------------------
        if any(w in low for w in ("exist", "file", "folder", "directory", "created")):
            import re as _re
            for m in _re.findall(r"[A-Za-z]:\\[^\s'\"]+", done_when):
                if os.path.exists(m):
                    return True, f"path exists on disk: {m}"

        # signal-word overlap with a recent successful tool result
        signals = {
            w.strip(".,:;()'\"").lower()
            for w in done_when.split()
            if len(w) > 3 and w.strip(".,:;()'\"").lower() not in self._VERIFY_STOP
        }
        if len(signals) >= 2:
            for h in reversed(history[-5:]):
                if "-> ok:" not in h:
                    continue
                hl = h.lower()
                hit = sum(1 for s in signals if s in hl)
                if hit >= max(2, len(signals) // 2):
                    return True, f"a successful tool result matches: {h[:160]}"
        return None

    def _open_windows(self) -> list[str]:
        """The titles on screen right now, or nothing if that failed."""
        try:
            out = self._registry.execute("ui_control", {"action": "windows"})
        except Exception:  # noqa: BLE001
            return []

        if not isinstance(out, dict) or out.get("status") == "error":
            return []

        return [
            str(w.get("title") or "")
            for w in (out.get("windows") or [])
            if isinstance(w, dict)
        ]

    def _finalize(self, result: TaskResult) -> None:
        # Compare what was verified against the CURRENT plan (replans can
        # grow result.plan with re-listed steps), not a raw list length.
        current = [s for s in result.plan]
        verified_set = set(result.verified)
        outstanding = [s for s in current if s not in verified_set]
        n_ok = len(verified_set)

        # If replans shrank the plan by more than a step below what we first
        # committed to, real work was likely dropped - don't call that
        # "done". A trim of one step is usually just consolidation.
        shrank = (
            self._first_plan_len > 0
            and len(current) < self._first_plan_len - 1
        )

        # A task whose last act failed did not end well, whatever the
        # steps before it managed. Alfred announced a saved screenshot
        # over a step that returned code 1, and then learned the whole
        # thing as a reusable skill, because everything up to the last
        # move had gone fine and nothing looked at how it finished.
        if result.status not in ("cancelled", "exhausted", "error"):
            acted = [s for s in result.steps if s.tool]
            if acted and not acted[-1].ok:
                result.unverified.append(
                    f"the last thing I tried ({acted[-1].tool}) failed"
                )

        # Read the finding out of the tool output BEFORE deciding how
        # the task went, because for a question the finding is how it
        # went. This used to run last, so "is there a folder on my
        # Desktop, and what is in it?" could run four successful
        # PowerShell calls, produce no answer whatsoever, and be
        # reported as done.
        result.answer = self._finding(result)

        if result.status in ("cancelled", "exhausted", "error"):
            base = {
                "cancelled": "Stopped at your request",
                "exhausted": "Ran out of time",
                "error": "Hit an error",
            }[result.status]
        elif (
            n_ok
            and not outstanding
            and not shrank
            and not _ended_badly(result)
            and _answers_the_goal(result)
            and _answered_the_question(result)
        ):
            result.status = "done"
            base = "Done"
        elif n_ok:
            result.status = "partial"
            base = "Partly done"
            if shrank and not outstanding:
                result.unverified.append(
                    "some of the original plan was dropped when I got stuck"
                )
            if not outstanding and not _answers_the_goal(result):
                result.unverified.append(
                    "every step worked, but none of them was about what you "
                    "asked for - say it again and I will plan it properly"
                )
            if not _answered_the_question(result):
                result.unverified.append(
                    "I did the looking but could not get an answer out of it"
                )
        else:
            result.status = "failed"
            base = "Couldn't do it"

        parts = [f"{base} on '{result.goal}'."]
        if result.verified:
            parts.append("Confirmed: " + "; ".join(result.verified) + ".")
        if result.unverified:
            parts.append(
                "Couldn't confirm: " + "; ".join(result.unverified) + "."
            )
        if result.skipped_confirmations:
            parts.append(
                "Left for you: " + "; ".join(result.skipped_confirmations) + "."
            )
        result.summary = " ".join(parts)

    # ----------------------------------------------------------------

    def _finding(self, result: TaskResult) -> str:
        """What the work found out, in a sentence.

        The summary reports what was done - "Confirmed: run PowerShell to
        check whether Steam is running" - and stops exactly short of the
        one thing that was asked. Spoken aloud that mostly passes,
        because the live model has the conversation around it and fills
        the gap. Sent to a phone it is useless: you asked whether Steam
        was open and were told that the question had been looked into.

        The answer is already sitting in the tool output. This just
        reads it back.
        """
        useful = [
            s for s in result.steps
            if s.ok and s.result not in (None, "", {}, [])
        ][-4:]
        if not useful:
            return ""

        # Room enough for the answer to be in there.
        #
        # Every step used to get 400 characters. "What is on my
        # Desktop?" runs one Get-ChildItem over thirty-three items,
        # which is several thousand - so the model was handed a listing
        # cut off partway through the Bs and, correctly, said it could
        # not answer from that. The task reported done with no finding
        # at all: Alfred had the answer in its hand and read out the
        # first tenth of it.
        #
        # The last step is the one that was asked about, so it gets the
        # room; anything before it is context.
        budget = 3000
        earlier = min(len(useful) - 1, 3)
        for_last = budget - earlier * 500

        trace = "\n".join(
            f"- {s.tool}: {_short(s.result, for_last if s is useful[-1] else 500)}"
            for s in useful
        )
        # A question that asks WHAT wants the things, not the count of
        # them. "One short sentence" turned "what is on my Desktop?"
        # into "your desktop has 31 items", "are there any .txt files?"
        # into "yes, there are 2", and - because it was summarising a
        # listing it had been told to compress - "is there a folder on
        # my Desktop?" into a folder called Desktop, which there is not.
        # The names were all sitting in the tool output.
        wants_a_list = _WANTS_THE_THINGS.search(result.goal or "")

        shape = (
            "Answer with the things themselves, named. Up to ten of "
            "them, comma-separated, most relevant first; if there are "
            "more say how many more. Do not answer with only a count - "
            "the count is not what was asked for."
            if wants_a_list else
            "Answer the request in one short sentence, as if replying "
            "to a text message. Lead with the answer itself - yes, no, "
            "the number, the name."
        )

        prompt = (
            f"REQUEST: {result.goal}\n\nWHAT CAME BACK:\n{trace}\n\n"
            f"{shape} Use only what is above - do not invent a name "
            "that is not in it.\n"
            # An empty result is a finding, not the absence of one.
            # "Is there a folder on my Desktop?" ran a command that
            # succeeded and listed nothing, which is the whole answer -
            # no - and Alfred said nothing at all, four times over.
            "A command that ran fine and returned nothing means there "
            "are none: say so plainly (\"no, there aren't any\") rather "
            "than saying nothing.\n"
            "Only if what came back genuinely cannot answer the "
            "request - it is about something else, or it failed - say "
            "NOTHING.\n\n"
            "Put your answer on its own line after the word ANSWER:"
        )
        try:
            line = self._fast_chat.generate(
                prompt, system=_THINKING_OFF, temperature=0.2,
                max_tokens=400 if wants_a_list else 200,
            ).strip()
        except Exception:  # noqa: BLE001
            return ""

        line = _answer_line(line)
        return "" if line.upper().startswith("NOTHING") else line[:300]

    # ----------------------------------------------------------------

    def _run_tool_step(
        self,
        index: int,
        decision: dict[str, Any],
        history: list[str],
        result: TaskResult,
        session_id: str | None,
    ) -> Step:
        tool = decision.get("tool")
        raw_args = decision.get("args")
        args = raw_args if isinstance(raw_args, dict) else {}
        thought = str(decision.get("rationale", "")).strip()

        proposal = Proposal(
            kind=ProposalKind.ACT, message=thought or f"use {tool}",
            tool=tool, args=args,
        )
        verdict = self._policy.evaluate(proposal)

        if verdict.verdict is Verdict.FORBID:
            history.append(
                f"[step {index}] REFUSED {tool}: {verdict.reason}"
            )
            return Step(index, thought, tool, args, "forbid",
                        {"refused": verdict.reason}, False)

        if verdict.verdict is Verdict.CONFIRM:
            if self._ask_user is not None:
                question = (
                    f"Sir, this step ({thought or tool}) is a bit risky - "
                    f"{verdict.reason}. Do you want me to go ahead?"
                )
                try:
                    approved = bool(self._ask_user(question))
                except Exception as exc:  # noqa: BLE001
                    print(f"[Task] ask_user failed: {exc}")
                    approved = False

                if not approved:
                    result.skipped_confirmations.append(
                        f"{tool} ({thought or 'no rationale'}) - you said no"
                    )
                    history.append(
                        f"[step {index}] {tool}: user declined. Skip it."
                    )
                    return Step(index, thought, tool, args, "declined",
                                {"declined": verdict.reason}, False)
                history.append(f"[step {index}] {tool}: user approved.")
                # fall through and execute
            else:
                # Nobody to ask. Say what was needed rather than letting
                # the step evaporate - a skipped step that is never
                # mentioned is indistinguishable from a lie.
                note = (
                    f"{tool} ({thought or 'no rationale'}) - needed your OK "
                    f"and there was no way to ask from here"
                )
                result.skipped_confirmations.append(note)
                history.append(
                    f"[step {index}] SKIPPED {tool}: needs the user's OK "
                    f"({verdict.reason}). Continue with other steps."
                )
                return Step(index, thought, tool, args, "confirm",
                            {"skipped": verdict.reason}, False)

        try:
            outcome = self._registry.execute(tool, args)
        except Exception as exc:  # noqa: BLE001
            outcome = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        ok = tool_succeeded(outcome)

        # Noted here, at the moment of doing it, because this is the only
        # place that knows what was actually done. Reconstructing it from
        # a log afterwards is guessing.
        if ok and self._undo is not None:
            try:
                self._undo.note_tool(tool or "", args, task=self._goal_now)
            except Exception:  # noqa: BLE001
                pass

        summary = summarize_result(outcome, 400)
        history.append(
            f"[step {index}] {tool}({_short(args, 160)}) -> "
            f"{'ok' if ok else 'FAILED'}: {summary}"
        )

        self._log(
            "task_step",
            {"index": index, "tool": tool, "args": args, "ok": ok,
             "result": summary},
            session_id,
        )

        # Said in the interface as it happens, in the words a person
        # would use, rather than as the tool call it was.
        LIVE.task_step(
            f"{tool}: {'done' if ok else 'failed'}", summary[:120]
        )

        return Step(index, thought, tool, args, "auto", outcome, ok)

    def _log(self, kind: str, payload: dict[str, Any], session_id: str | None):
        if self._audit is not None:
            try:
                self._audit.record(kind, payload, session_id)
            except Exception:  # noqa: BLE001
                pass


# ====================================================================
# helpers
# ====================================================================


# A plan step that is really a tool call: "ui_control key keys='^a'",
# "system_info query='disks'", "powershell -Command ...".
_TOOL_SYNTAX = re.compile(
    r"\b(ui_control|desktop_control|system_info|network_info|open_app|"
    r"run_task|computer_screenshot)\b\s*[\w'\"{(=-]",
)

# Steps that are really the executor's internal business: finding,
# focusing or reading the control tree.
_MICRO_ACTION = re.compile(
    r"^(find|locate|focus|identify|read|inspect)\b.*\b"
    r"(control|edit box|text ?box|element|tree|field|handle)\b|"
    r"^(focus|activate)\s+the\b",
    re.I,
)

_MUTATION_INTENT = (
    "clean up", "tidy", "organi", "declutter", "sort out", "fix", "delete",
    "remove", "move", "rename", "archive", "clear out", "free up", "uninstall",
    "disable", "enable", "turn on", "turn off", "set up", "install", "close",
)
_MUTATION_VERBS = (
    "delete", "remove", "move", "rename", "create", "run ", "click", "type",
    "set ", "enable", "disable", "close", "open", "install", "uninstall",
    "stop", "start", "change", "turn ", "write", "compress", "archive", "play",
)


def _enum_hints(schema: dict[str, Any]) -> str:
    """'action: tree|click|type|get; target: alfred|user' from a JSON schema."""
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return ""
    bits = []
    for key, spec in props.items():
        if isinstance(spec, dict) and isinstance(spec.get("enum"), list):
            vals = "|".join(str(v) for v in spec["enum"][:12])
            bits.append(f"{key}: {vals}")
    return "; ".join(bits)


def _parse(raw: str) -> dict[str, Any] | None:
    text = raw.strip()

    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.lower().startswith("json"):
            text = text[4:]

    text = text.strip()

    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def _answer_line(raw: str) -> str:
    """The answer, not the working out.

    "detailed thinking off" is a request, not a guarantee - the bench
    got back "We need to answer: \"What version of Windows is this?\"
    The output shows" as Alfred's reply to the user. So the sentence is
    asked for behind a marker and taken from there; failing that, from
    the end, because when a model does think out loud the conclusion is
    the last thing it says, never the first.
    """
    lines = [l.strip() for l in (raw or "").splitlines() if l.strip()]  # noqa: E741
    if not lines:
        return ""

    # Anchored to the start of a line, because "We need to answer:" is
    # not the answer - it is a model narrating its way towards one, and
    # a loose search finds that first. Scanned from the end, because
    # that is where a conclusion lives.
    for line in reversed(lines):
        bare = line.lstrip("*#->` ").strip()
        if bare.upper().startswith("ANSWER:"):
            said = bare[len("ANSWER:"):].strip()
            if said:
                return said

    # The last line can be the marker itself with nothing after it,
    # which is how "ANSWER:" came back to the user as the answer.
    last = lines[-1]
    return "" if last.rstrip().upper().rstrip(":") == "ANSWER" else last


def _why_it_failed(step: Step) -> str:
    """The words that say what went wrong.

    Tools disagree about where they put it: PowerShell has stderr, others
    have error or message. Reading only "error" meant every PowerShell
    failure on the machine collapsed into one signature whose detail was
    the string "auto" - the step's verdict, not its error - so a bad
    enum name and an access denial counted as the same wall.
    """
    result = step.result
    if isinstance(result, dict):
        for key in ("error", "stderr", "message", "reason", "detail"):
            text = str(result.get(key) or "").strip()
            if text:
                command = str(result.get("command") or "")
                return _without_echo(text, command)[:300]
        status = str(result.get("status") or "").strip()
        if status and status != "success":
            return status
    elif isinstance(result, str) and result.strip():
        return result.strip()[:300]

    return "failed"


def _without_echo(text: str, command: str) -> str:
    """Shells repeat the failing command back before saying what broke."""
    command = command.strip()
    if len(command) < 40 or command not in text:
        return text
    return text.replace(command, "").lstrip(" :\r\n") or text


# Words that say nothing about WHICH job this is. A goal made only of
# these - "how long has this PC been up?" - is one the check has no
# opinion about, and says so by leaving fewer than two words behind.
_GOAL_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "at", "for",
    "with", "from", "into", "that", "this", "then", "when", "what",
    "please", "user", "users", "alfred", "make", "get", "have", "there",
    "some", "any", "just", "also", "about", "current", "currently",
    "check", "find", "look", "show", "tell", "give", "want", "need",
    "using", "use", "run", "open", "start", "launch", "learn", "routine",
    "set", "put", "take", "do", "does", "done",
    "how", "has", "had", "been", "being", "was", "were", "are", "its",
    "you", "your", "my", "mine", "me", "can", "could", "would", "should",
    "much", "many", "long", "more", "most", "very", "still", "right",
    "now", "out", "off", "over", "back", "here", "thing", "things",
    "again", "which", "whether", "will", "shall", "let",
}


def _words(text: str) -> set[str]:
    """Content words, split the way a tool name is spelled.

    On word boundaries rather than whitespace: `system_info` has to
    come apart into `system` and `info`, or a step that ran exactly the
    right tool reads as unrelated to the question it answered. Three
    letters, not four, because RAM and CPU are the whole subject of the
    sentences they appear in.
    """

    return {
        stripped
        for word in re.findall(r"[a-z0-9']+", (text or "").lower())
        # An apostrophe belongs inside a word (Sana'a, don't) and never
        # at its edge, where it is a quote mark: 'Stretch' has to come
        # out as the same word as Stretch.
        if (stripped := word.strip("'")) and len(stripped) >= 3
        and stripped not in _GOAL_STOP
    }


def _answers_the_goal(result: TaskResult) -> bool:
    """Is the work that was verified about the thing that was asked?

    Every step is verified against its own done_when, which means a
    plan can be fully verified and still be a plan for something else.
    That is exactly what happened to "Search for how to fish": the
    planner opened File Explorer at the Desktop, the step did what it
    said it would, and the task was reported done. Nothing in the chain
    had ever compared the work against the request.

    Deliberately blunt - it asks for a single word in common, not for a
    good answer. A task that shares no content word at all with what was
    asked is not a task that did it.
    """

    # A question's deliverable is its answer, and an answer is not
    # obliged to repeat the question: "192.168.1.42" is the right reply
    # to "what is my local IPv4" and shares no word with it. Word
    # overlap cannot judge those, so it does not try. The failure this
    # guards against - a plan that went and did something else - shows
    # up as a task that finished with nothing found.
    if (result.answer or "").strip():
        return True

    wanted = _words(result.goal)

    # One content word is not enough to judge on: "Open Notepad" against
    # a step called "Notepad" is a match either way, and a one-word goal
    # that misses would more likely be a synonym than a wrong plan.
    if len(wanted) < 2:
        return True

    # What Alfred says it did, not what its tools happened to return.
    # A folder listing can contain any word at all - the Desktop in the
    # "how to fish" run held a game called How to Fish - so matching
    # against raw results would make the check agree with itself.
    haystack = " ".join(
        [
            *result.verified,
            *result.plan,
            result.answer or "",
            result.replayed_template,
        ]
    )

    return bool(wanted & _words(haystack))


# Capitalised words that begin a sentence rather than name a program.
_NOT_A_NAME = {
    "the", "then", "when", "and", "but", "this", "that", "these", "those",
    "open", "opens", "opened", "window", "windows", "visible", "showing",
    "running", "launched", "file", "folder", "there", "after", "once",
    "user", "alfred", "desktop", "screen", "text", "content", "contents",
    "its", "should", "must", "will", "shows", "displayed", "confirm",
}


# A request that wants the things, not a tally of them.
_WANTS_THE_THINGS = re.compile(
    r"\bwhat(?:'s| is| are)?\s+(?:on|in|inside|under|there)\b|"
    r"\bwhat\s+\w*\s*(?:files?|folders?|apps?|programs?|shortcuts?|"
    r"windows?|items?|documents?|photos?|videos?)\b|"
    r"\bwhich\b|\blist\b|\bshow me\b|\bname the\b|"
    r"\bare there any\b|\banything\b|\bwhat else\b|"
    r"\bcontents? of\b|\bwhat do I have\b",
    re.I,
)


def _names_in(text: str) -> set[str]:
    """The things a done_when sentence names, for looking up on screen.

    Quoted names, filenames with an extension, and Capitalised words -
    between them that covers "research.txt is open", "the 'Save As'
    window appears" and "Notepad is showing the text".
    """

    names: set[str] = set()

    names.update(re.findall(r"['\"]([^'\"]{2,40})['\"]", text))
    names.update(re.findall(r"\b([\w-]+\.[a-z]{2,4})\b", text, re.I))
    names.update(
        word for word in re.findall(r"\b([A-Z][\w+.-]{2,25})\b", text)
        if word.lower() not in _NOT_A_NAME
    )

    return {name.strip() for name in names if name.strip()}


# A goal that is asking something, rather than telling Alfred to do
# something.
_A_QUESTION = re.compile(
    r"^\s*(what|which|who|whose|where|when|why|how|is|are|was|were|do|"
    r"does|did|can|could|will|would|should|have|has|am|any)\b",
    re.I,
)


def _was_a_question(goal: str) -> bool:
    text = (goal or "").strip()
    return text.endswith("?") or bool(_A_QUESTION.match(text))


def _answered_the_question(result: TaskResult) -> bool:
    """If it was asked something, did it come back with anything?

    "Is there a folder on my Desktop, and what is in it?" ran four
    PowerShell calls, every one of them successful, produced no finding
    at all, and reported done. Every step verified, so nothing
    downstream had any reason to doubt it - and the user is told the
    question has been looked into and not what the answer is.

    Done is for a question that got an answer.
    """

    if not _was_a_question(result.goal):
        return True

    return bool((result.answer or "").strip())


def _ended_badly(result: TaskResult) -> bool:
    """Did the last thing Alfred actually did fail?

    Everything else about finishing looks at the plan - which steps were
    ticked off - and a plan can be fully ticked off by a run whose final
    move fell over. That is how a screenshot that was never saved came
    to be reported as done and then learned as a routine.
    """
    acted = [s for s in result.steps if s.tool]
    return bool(acted) and not acted[-1].ok


def _short(value: Any, limit: int = 240) -> str:
    try:
        text = json.dumps(value, default=str) if not isinstance(value, str) else value
    except Exception:  # noqa: BLE001
        text = str(value)

    return text if len(text) <= limit else text[:limit] + "…"
