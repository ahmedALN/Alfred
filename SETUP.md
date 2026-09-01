# Setting Alfred up

From nothing to a working assistant. About twenty minutes, most of it
downloads.

Alfred is Windows-only. It drives the desktop through Windows UI
Automation, so there is no Linux or macOS version and there is not
going to be one.

---

## Before you start

| you need | why | cost |
|---|---|---|
| **Windows 10 or 11** | it drives your actual desktop | — |
| **Python 3.11+** | [python.org](https://python.org) — tick *Add to PATH* | free |
| **A Gemini API key** | voice, and reasoning by default | free tier is enough |
| **.NET 8+ SDK** | [dotnet.microsoft.com](https://dotnet.microsoft.com) — builds the desktop-control helpers | free |
| **A microphone** | it is a voice assistant | — |

Optional, and each adds one capability:

| optional | gives you |
|---|---|
| [Ollama](https://ollama.com) | local reasoning, so Alfred keeps working when the Gemini quota runs out |
| A Google account | mail, calendar and coursework |
| WhatsApp | talking to Alfred from your phone |

---

## 1. Get the code

```bash
git clone https://github.com/ahmedALN/Alfred.git
cd Alfred
```

## 2. Make a virtualenv and install

```bash
python -m venv .venv
```

```bash
.venv\Scripts\pip install -r requirements.txt
```

## 3. Run the setup checker

```bash
.venv\Scripts\python -m src.setup
```

It checks your Python packages, looks for Ollama and the .NET SDK,
builds the native helpers, and creates a `.env` from `.env.example` if
you do not have one. It is safe to run again whenever something looks
wrong.

## 4. Add your Gemini key

Get one at [aistudio.google.com](https://aistudio.google.com) — it is
free, and takes a minute. Open `.env` and put it on the first line:

```
GEMINI_API_KEY=your-key-here
```

**`.env` is gitignored and must stay that way.** It is the one file in
this project that is genuinely secret.

## 5. Start it

```bash
.venv\Scripts\python -m src.main
```

Alfred greets you, and the interface opens. Say **"Hey Jarvis"** —
the bundled wake word, downloaded on first run — or press
**Ctrl+Alt+K**, and talk to it.

To use your own phrase instead, set `ALFRED_WAKE_PHRASE=hey alfred` in
`.env`. That route uses Vosk and needs no training.

If you would rather it did not open a window on startup, set
`ALFRED_INTERFACE_ON_START=false` in `.env`.

---

## 6. Make it start with the PC

```bash
.venv\Scripts\python -m src.autostart install
```

This drops a shortcut in your Startup folder that launches Alfred with
`pythonw.exe` — no console, nothing in the taskbar, nothing to close by
accident. A watchdog restarts it if it crashes, up to six times an
hour, then stops and tells you to look at `logs/alfred.log`.

To undo it:

```bash
.venv\Scripts\python -m src.autostart uninstall
```

---

## Optional: local reasoning with Ollama

Without this, everything goes to Gemini, and when the free quota runs
out Alfred stops thinking. With it, Alfred falls back to a local model
and keeps working — slower, and less accurate, but working.

Install [Ollama](https://ollama.com), then:

```bash
ollama pull qwen3.5:4b
```

```bash
ollama pull nomic-embed-text
```

`qwen3.5:4b` is deliberate, not a compromise. A 9B model is worse at
this job on an 8 GB card: measured on the executor's real prompt, the
4B chose the right tool 3 times out of 3 in 5.2s where the 9B managed
2 of 3 in 8.4s and once emitted malformed JSON. Bigger is not better at
producing a strict format on limited VRAM.

## Optional: mail, calendar and coursework

See **[docs/google.md](docs/google.md)**. It is a Google Cloud console
walkthrough — about ten minutes, and the one step people miss is
adding the scopes under *Data Access* before signing in.

Alfred holds `gmail.modify`, which has no send in it. It cannot email
anyone, and that is enforced by Google rather than by this code.

## Optional: WhatsApp

See **[docs/whatsapp.md](docs/whatsapp.md)**. You pair it like WhatsApp
Web, and then message yourself to reach Alfred from anywhere.

---

## Living with it

| | |
|---|---|
| **Wake word** | "Hey Jarvis" out of the box; set `ALFRED_WAKE_PHRASE` for your own |
| **Talk hotkey** | `Ctrl+Alt+K` |
| **Show/hide the interface** | `Ctrl+Alt+I` |
| **What it costs** | `python -m src.costs` |
| **What it knows** | `python -m src.status` |
| **Is Google linked** | `python -m src.workspace status` |

The interface is the thing to open first. It shows what Alfred is
doing, what it believes about you, and lets you correct any of it —
see **[docs/interface.md](docs/interface.md)**.

---

## When it goes wrong

**Nothing happens when I say the wake word.** Check the microphone is
the default recording device. `ALFRED_WAKE_ENABLED=false` disables the
wake word and leaves the hotkey.

**"Could not start pairing: client is nil"** — WhatsApp only. Run
`python -m src.whatsapp pair` again; it needs a live connection before
it can show you a code.

**Alfred hears itself and answers its own voice.** Half-duplex is on
by default and should prevent this; if it is happening, check
`ALFRED_HALF_DUPLEX` has not been set to `false`, and lower your
speaker volume — the mic gate cannot beat a loud enough speaker.

**It is using too much quota.** `python -m src.costs` shows where it
goes. The `idle` column is the brain thinking on its own every 90
seconds — raise `ALFRED_BRAIN_TICK_SECONDS` to spend less.

**A PowerShell window flashed up.** It should not. Everything Alfred
spawns while running carries `CREATE_NO_WINDOW`; if you see one, that
is a bug worth reporting.

**It believes something wrong about me.** Open the interface
(`Ctrl+Alt+I`) → **memory**, and correct or delete it. That is what the
window is for.

---

## What stays on your machine

Everything except the model calls. Alfred writes its stores next to the
code — memories, skills, tasks, your calendar, WhatsApp contacts and
message keys — and every one of them is gitignored. Nothing is uploaded
anywhere, there is no telemetry, and the interface listens on
`127.0.0.1` behind a token that is regenerated every time Alfred
starts.

What does leave: the text and audio of what you say to it, to Google's
Gemini API, and only that.
