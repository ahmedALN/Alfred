# Alfred

A voice-driven AI that lives on your Windows PC. It starts with the machine and
stays resident: you wake it with a phrase, talk to it, and it acts on the
computer — opens apps, runs commands, drives its own desktop. A background
"brain" watches the system and speaks up when something needs attention. It
remembers things between sessions, and it can take on multi-step jobs and report
back.

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

```bash
python -m pip install -r requirements.txt
python -m src.setup      # checks deps, pulls models, builds native helpers, writes .env
python -m src.main
```

`src/setup.py` is safe to re-run. To do it by hand: copy `.env.example` to
`.env`, add `GEMINI_API_KEY`, set `ALFRED_AI_PROVIDER=ollama`, then
`dotnet build -c Release` the projects under `src/windows/native/`.

## Talking to it

Say **"Hey Alfred"** (or press the hotkey, default `Ctrl+Alt+K`). Alfred wakes,
you talk, and it drops back to sleep after ~30 s of silence.

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
| "what can you do" | an accurate rundown of its tools and limits |

Game mode also auto-engages when a fullscreen game holds the foreground
(`ALFRED_GAME_AUTODETECT`).

## Everyday commands

```bash
python -m src.status                 # is it running? what has it done today?
python -m src.memory_cli list        # what it remembers  (search / forget / edit / dedupe / export)
python -m src.autostart install      # start at login     (uninstall / status)
python -m src.voice.train_wakeword   # record samples for a custom "Hey Alfred"
```

Logs: `logs/alfred.log`. Brain activity: `alfred_brain_audit.jsonl`.

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

## Tests

```bash
python -m pytest -q
```

Some tests need the built `DesktopBridge.exe` (they exercise the real bridge).
