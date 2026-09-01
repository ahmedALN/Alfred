# Alfred's interface

A window showing what Alfred knows, what it is doing, and what it
believes — and letting you correct any of it.

It is **optional**. Nothing opens it but you.

## Opening it

| how | what |
|---|---|
| **Ctrl+Alt+I** | the hotkey, set by `ALFRED_INTERFACE_HOTKEY` |
| *"Alfred, open your interface"* | by voice |
| a WhatsApp message saying the same | from your phone |
| `python -m src.ui` | standalone, without Alfred running at all |

The address, with this run's key in it, is written to
`.alfred_interface_url` (gitignored) if you would rather open it in an
ordinary browser.

Closing the window **hides** it rather than quitting, so the second
opening is instant. Asking again when it is already open brings it to
the front instead of drawing a second one.

## The panels

| panel | what is in it |
|---|---|
| **overview** | the reactor, what Alfred is doing, and the numbers |
| **talk** | type to Alfred, and push‑to‑talk into the live voice session |
| **logs** | Alfred's own narration, streaming as it happens |
| **memory** | every fact it has learned about you — editable, deletable |
| **your life** | deadlines, people waiting on you, what you are working on |
| **skills** | what it has learned to do, and what it believes it cannot |
| **tasks** | everything it has been asked to do and how that went |
| **automations** | scheduled things, and a switch to turn each off |
| **screen** | what Alfred can currently see of your desktop |

`Alt`+`1`…`9` jumps between them.

## Correcting what it believes

This is the point of the thing. Alfred learns on its own, and until now
a wrong belief stayed wrong forever.

- **a fact it got wrong** — correct it or delete it (memory panel). A
  correction clears the stored embedding, because that vector described
  the old wording and would keep recalling the fact for the old question.
- **something it thinks it cannot do** — delete the limitation and it
  will try again. These are learned from failures, and a limitation
  recorded on one bad afternoon otherwise stops it attempting that
  thing for good.
- **a skill that misbehaves** — disable it, or delete it.
- **something on your plate that is done, or was never yours** — settle
  it or drop it.

## Why it is safe to run

The server binds to `127.0.0.1` and nothing else. That is necessary and
not sufficient: **any web page you have open can also make requests to
loopback**, and this server can read your mail, your memories and your
screen.

So every request carries a token, generated fresh each time Alfred
starts and passed to the window in its URL. A page that has not been
told the key gets 403 — and the `Host` header is checked too, which
closes the DNS‑rebinding route to the same thing.

The browser can only name actions from a fixed list. There is no
endpoint that runs arbitrary SQL.

## What it is made of

No build step and no framework: the page is HTML, CSS and three small
ES modules, served by the same process that is Alfred.

- **animations** are CSS and canvas — nothing downloaded, nothing a
  bitmap, so it stays sharp at any size
- **sounds** are synthesised with the Web Audio API rather than sampled,
  which is why there are no audio files and no licence to honour. They
  duck automatically while Alfred is speaking
- **fonts** (Orbitron, Rajdhani, Inter — Open Font License) and **icons**
  (Lucide, MIT) were fetched once and are kept in `src/ui/static`, so the
  window draws with no internet

The window itself is a separate process. pywebview wants the main
thread and Alfred's main thread is busy being Alfred, so the split is
structural — and it is what lets closing merely hide.

## Settings

```
ALFRED_INTERFACE_HOTKEY=ctrl+alt+i
```

The port is 8756, loopback only.

## If something looks wrong

**The window says "offline".** Alfred is not running. The interface
still works — it reads the store files directly, which is exactly what
you want just after a crash. Only talking, the microphone and the
screen need a live Alfred.

**No sound.** The speaker icon at the bottom of the rail toggles it,
and the choice is remembered. Effects also go quiet on their own while
Alfred is speaking.

**A stale window from a previous run.** The token dies with the Alfred
that made it, so a window left over from a crashed run can never
authenticate again. Opening the interface clears those first.
