# Alfred

A voice-driven AI that lives on your Windows PC. It starts with the machine and
stays resident: you wake it with a phrase, talk to it, and it acts on the
computer — opens apps, runs commands, drives its own desktop. A background
"brain" watches the machine *and you* — what is overdue, how long you have been
in one window, whether the mailbox link has lapsed — and speaks up when
something needs attention. It remembers things between sessions, and it can take
on multi-step jobs and report back.

Voice uses the Gemini Live API. Everything else (reasoning, memory, screen
understanding) runs locally through [Ollama](https://ollama.com) by default — no
other API keys, no per-token cost.

## Requirements

- Windows 10/11, Python 3.11+
- A Gemini API key ([aistudio.google.com](https://aistudio.google.com)) — voice only
- [.NET 8+ SDK](https://dotnet.microsoft.com) — for the desktop-control helpers
- [Ollama](https://ollama.com) with `qwen3.5:4b` + `nomic-embed-text` — for the
  local reasoning/memory/vision (an 8 GB GPU runs `qwen3.5:4b` comfortably)
- A microphone and speakers (or a headset — see *Two voices / echo* below)

## Setup

**→ [SETUP.md](SETUP.md)** walks through it properly. The short version:

```bash
python -m venv .venv
```

```bash
.venv\Scripts\pip install -r requirements.txt
```

```bash
.venv\Scripts\python -m src.setup
```

Put your Gemini key in `.env`, then `.venv\Scripts\python -m src.main`.
`src/setup.py` is safe to re-run whenever something looks wrong.

## Its interface

Press **Ctrl+Alt+I**. A window opens showing Alfred's logs, what it is
doing, what it has learned about you, its skills, its scheduled jobs, and
what it can see of your screen — and lets you correct any of it. A wrong
memory or a limitation it learned on a bad afternoon can be deleted, so
it stops being permanent.

It opens with Alfred by default (`ALFRED_INTERFACE_ON_START=false` to
stop that), and the same key hides it again. Details in
**[docs/interface.md](docs/interface.md)**.

## Talking to it

Say the wake word (bundled **"Hey Jarvis"**, or set your own — see below) or
press the hotkey (default `Ctrl+Alt+K`). Alfred wakes, you talk, and it drops
back to sleep after ~30 s of silence.

For a custom **"Hey Alfred"**: `python -m src.voice.setup_wakeword` once, then
put `ALFRED_WAKE_PHRASE=hey alfred` in `.env`.

Want the wake word to only work for *your* voice — not a housemate, a TV, or
a podcast saying it? `python -m src.voice.enroll_voice` records a few takes
and enrolls a voiceprint (192 numbers, stored locally, never your actual
voice); once that file exists, a phrase match from anyone else is ignored.
`--test` checks a take against it without changing anything, and
`ALFRED_SPEAKER_VERIFY_ENABLED=false` in `.env` turns the check back off.

| Say | What happens |
|---|---|
| "open Spotify" / "open youtube" | launches it on Alfred's own desktop |
| "how much RAM is free" / "what ports are open" | structured system/network read |
| "organize my downloads folder" | hands it to the background task agent |
| "stop the task" / "cancel that" | interrupts the running task |
| "game mode" / "free up the GPU" | unloads local models, pauses background work |
| "back to normal" | restores everything |
| "do not disturb" | silences the proactive brain for this session |
| "stop telling me about disk space" | permanently suppresses that topic |
| "forget my router password" | deletes a memory (asks first) |
| "put my pc to sleep" / "lock the screen" | sleep, lock, restart, shut down or sign out |
| "learn how to search Steam for a game" | designs a reusable routine and keeps it |
| "what can you do" | an accurate rundown of its tools and limits |

Game mode also auto-engages when a fullscreen game holds the foreground
(`ALFRED_GAME_AUTODETECT`).

## Learning new things

Alfred picked up routines by accident: do a job well once and the
sequence that worked was distilled into a skill. That gets better at
what it already does and never at what it has just been asked for.

Ask it to **learn** something and it designs the routine instead —
working out the steps, checking every one against the tools that
actually exist, and keeping the result. A designed routine is saved
unproven and at low confidence, because it is a plan that has never
been run; the first real attempt is what earns it a promotion.

```
"learn how to search Steam for a game"
"what have you learned to do"
"forget how to do that"
```

Anything it invents is rejected before it is saved: a step naming a
tool that is not there, an argument the schema does not take, a
routine with no steps or twelve of them. A skill that fails at step one
is worse than no skill, because it fails every time and further from
the cause.

## Everyday commands

```bash
python -m src.doctor                 # is everything in working order? (--quiet for problems only)
python -m src.models                 # which model answers, and how fast  (add groq <key> to wire one in)
python -m src.status                 # running? memory, brain + Gemini usage today
python -m src.memory_cli list        # what it remembers  (search / forget / edit / dedupe / export)
python -m src.knowledge seed         # load the built-in Windows playbook into memory (do this once)
python -m src.skills list            # learned routines  (show / forget / disable / enable / dedupe)
python -m src.episodes recent        # what it actually did lately  (search / prune)
python -m src.apps list              # how to work inside each app  (show / note / forget)
python -m src.childsession probe     # can this PC give Alfred its own desktop?
python -m src.startup sessions       # what each Windows session costs in RAM  (list / trim)
python -m src.autostart install      # start at login via the watchdog (uninstall / status)
python -m src.costs                  # what a month of this costs to run
python -m src.workspace status       # is Google linked, and what may it do
python -m src.ui                     # the interface on its own, without Alfred running
python -m src.watchdog               # run Alfred with crash-restart supervision
python -m src.voice.setup_wakeword   # download the model for a custom wake phrase
python -m src.voice.enroll_voice     # only your voice wakes it  (--test / --reset)
```

**Planning model:** multi-step tasks are planned (and re-planned) by
`ALFRED_AI_PLAN_MODEL` — `gemini-flash-lite-latest` by default, because it
is the only key you must have and it answers a planner-sized prompt in
0.6-0.9s where a bigger model takes 2-14. Add a free NVIDIA key from
build.nvidia.com and the 120B Nemotron becomes the next rung down; the
local model is always last, because it cannot run out of quota.
Execution of each step uses the fast local model.

**Its own desktop:** say *"without disturbing me, do X"* and Alfred opens a
second, invisible Windows session (a [child
session](https://learn.microsoft.com/en-us/windows/win32/termserv/child-sessions))
and works there — it can open apps, click and type without touching your screen,
focus or clipboard, and it survives you locking the PC. When the task finishes it
closes whatever it opened. Setup is three one-off elevated commands in
`scripts/`; run `python -m src.childsession probe` first to check your machine.
Games are deliberately *not* isolated — run those normally.

**Working inside apps:** "open Spotify and play something by X", "open Steam and
launch Y" — Alfred drives an app's real controls through the accessibility tree
(`ui_control`): wait for the app to be usable, read its controls, then click,
type, select, scroll or use its menus by name. It **will not type passwords,
PINs or security codes** — for a sign-in it gets you to the login screen and
hands over.

**Getting better:** four layers, from hand-written to fully learned.

- a built-in Windows **playbook** (`python -m src.knowledge seed`) — the good way
  to do common things (how to drive Spotify, which cmdlet lists ports)
- **app memory** — the real window title and the control names that worked in
  each app, so the *second* task in an app skips the exploration the first one
  paid for. `python -m src.apps list`
- **skills** — after a task finishes *and every step verifies*, its tool sequence
  becomes a template; the same request next time skips planning and just runs
  (`play a {artist} song on Spotify` learned once, replays for any artist)
- **lessons** — a failed step becomes a durable correction fact

Risky routines are confirmed out loud before being saved. Alfred reports only
what it could verify — never "done" for a step it couldn't confirm.

Logs: `logs/alfred.log`. Brain activity: `alfred_brain_audit.jsonl`.

**Quota fallback:** if the Gemini voice quota is exhausted, Alfred switches to a
fully local voice loop (faster-whisper + your chat model + Piper TTS) for a few
minutes, then retries the cloud. Say *"switch back"* to end it early.

**Protecting risky actions:** set `ALFRED_VOICE_PASSPHRASE` and every dangerous
action (stop-service, firewall change, `rm -rf`, …) needs the passphrase spoken
first. Catastrophic actions are always refused.

**Gaming:** *"game mode"* (or automatically on a fullscreen game) unloads the
local models and pauses background work; *"back to normal"* restores it.

## Switching the reasoning backend

`.env`:

```
ALFRED_AI_PROVIDER=ollama         # or: gemini | openai
ALFRED_AI_CHAT_MODEL=qwen3.5:4b   # per-capability model overrides
ALFRED_AI_VISION_MODEL=qwen3.5:4b
```

`openai` targets any OpenAI-compatible endpoint (NVIDIA NIM, vLLM, LM Studio,
llama.cpp server, Groq, OpenAI) via `ALFRED_OPENAI_BASE_URL` /
`ALFRED_OPENAI_API_KEY`. Voice is always Gemini Live.

## Safety

Every action Alfred takes — asked for by voice or decided by the brain — passes a
policy gate:

- **catastrophic** (format/wipe a drive, delete `C:\Windows`, disable tamper
  protection): refused. Alfred tells you to do it yourself.
- **dangerous** (stop services, firewall/Defender changes, add accounts,
  scheduled tasks, `rm -rf`, fetch-and-run): Alfred explains the risk and asks;
  it proceeds only on your yes.
- **everything else**: runs.

`ALFRED_BRAIN_AUTONOMY` (`ask` / `auto_reversible` / `full`) tunes how much the
*background* brain does on its own; the catastrophic/dangerous gates apply
regardless.

A command is judged on what it means rather than on how it is spelled: aliases
are expanded, backticks stripped, split strings rejoined, any `-EncodedCommand`
payload decoded and a wrapped shell unwrapped, so `gci ... | ri -Force` and
`powershell -enc <base64>` are read as the deletes they are.

## Troubleshooting

- **Two voices / Alfred talks to itself** — you had two instances running (now
  prevented by a lock), or the mic is hearing the speakers. Use a headset, or
  keep `ALFRED_HALF_DUPLEX=true` (default — mutes the mic while Alfred speaks).
- **Local model is slow / hangs** — `qwen3.5:9b` doesn't fit an 8 GB GPU; use
  `qwen3.5:4b`. Alfred already disables Ollama "thinking" mode.
- **"model not found" on start** — Gemini model IDs move; set `GEMINI_LIVE_MODEL`
  / `GEMINI_TEXT_MODEL` in `.env` to current ones.
- **Hotkey does nothing** — the combo is taken by another app; set
  `ALFRED_HOTKEY` to a free one. The wake word still works.
- **Desktop control can't reach the agent** — the `.NET` helpers aren't built;
  run `python -m src.setup` or `dotnet build` them.

## Layout

```
src/
  ai/            Gemini Live session; swappable chat/embed/vision providers
  brain/         perception -> deliberation -> policy; task agent + queue; audit log
  voice/         wake word, hotkey, activation (conversation window)
  memory/        SQLite fact store, embeddings, distillation, dedup
  tools/         the capabilities the model can call
  windows/       PowerShell, app launcher, virtual desktops, native IPC
  windows/native/  C# helpers (DesktopBridge, ChildInputAgent, ...)
  resource_mode.py   game / low-resource mode
  main.py            wires it all together
```

## Checking it over

```bash
python -m src.doctor
```

Seven sections, each looking at the real thing rather than at a setting:
whether every reading a collector produces reaches something that can act
on it, whether the command gate still refuses a base64 payload, whether the
calls that were once refused over an argument's name now get through,
whether the stores open, which model will actually answer, and whether the
native helpers are built. `--quiet` prints only what is wrong; the exit code
is 0 when nothing is, so it can be run from a scheduled task and believed.

Several of those checks exist because the answer was *no* and nothing said
so — three collectors ran every ninety seconds for days with nothing
downstream reading them, and the gate called seven destructive one-liners
ordinary.

## Tests

```bash
python -m pytest -q
```

Some tests need the built `DesktopBridge.exe` (they exercise the real bridge).
Everything else runs anywhere, and runs on every push — see
[.github/workflows/tests.yml](.github/workflows/tests.yml).

## Licence

MIT — see [LICENSE](LICENSE). Do what you like with it.
