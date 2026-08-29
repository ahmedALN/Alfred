"""
python -m src.status  -  a read-only snapshot of what Alfred is doing.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _lock_holder() -> int | None:
    lock = Path(tempfile.gettempdir()) / "alfred.lock"
    try:
        pid = int(lock.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        import psutil

        if psutil.pid_exists(pid):
            return pid
    except Exception:  # noqa: BLE001
        return pid
    return None


def _audit_summary(db_path: Path) -> dict[str, object]:
    if not db_path.exists():
        return {"available": False}

    out: dict[str, object] = {"available": True}
    today = datetime.now(timezone.utc).date().isoformat()

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            "SELECT kind, COUNT(*) c FROM brain_audit "
            "WHERE created_at >= ? GROUP BY kind",
            (today,),
        ).fetchall()
        out["today"] = {r["kind"]: r["c"] for r in rows}

        spoken = conn.execute(
            "SELECT payload FROM brain_audit WHERE kind='spoken' "
            "ORDER BY id DESC LIMIT 3"
        ).fetchall()
        msgs = []
        for r in spoken:
            try:
                p = json.loads(r["payload"])
                if not p.get("suppressed"):
                    msgs.append(p.get("text", "")[:100])
            except Exception:  # noqa: BLE001
                pass
        out["recent_spoken"] = msgs

        note = conn.execute(
            "SELECT payload FROM brain_audit WHERE kind='tick' "
            "AND payload LIKE '%note%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if note:
            try:
                out["last_state_note"] = json.loads(note["payload"]).get("note")
            except Exception:  # noqa: BLE001
                pass

        conn.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)

    return out


def _fact_count(db_path: Path) -> int | None:
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        n = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        conn.close()
        return int(n)
    except Exception:  # noqa: BLE001
        return None


def _ollama_ps() -> list[str]:
    try:
        out = subprocess.run(
            ["ollama", "ps"], capture_output=True, text=True, timeout=8
        ).stdout.strip().splitlines()
        return out[1:] if len(out) > 1 else ["(no models loaded)"]
    except Exception:  # noqa: BLE001
        return ["(ollama not reachable)"]


def _tasks(db_path: Path) -> list[str]:
    tp = _ROOT / "alfred_tasks.sqlite3"
    if not tp.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{tp}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT goal, status FROM tasks ORDER BY id DESC LIMIT 5"
        ).fetchall()
        conn.close()
        return [f"[{r['status']}] {r['goal'][:70]}" for r in rows]
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    audit_db = _ROOT / os.getenv("ALFRED_BRAIN_AUDIT_DB", "alfred_brain_audit.sqlite3")
    mem_db = _ROOT / os.getenv("ALFRED_MEMORY_DB", "alfred_memory.sqlite3")

    pid = _lock_holder()
    print("Alfred status")
    print("=" * 40)
    print(f"running        : {'yes (pid ' + str(pid) + ')' if pid else 'no'}")

    facts = _fact_count(mem_db)
    print(f"memories       : {facts if facts is not None else 'n/a'}")

    audit = _audit_summary(audit_db)
    if audit.get("available"):
        today = audit.get("today", {})
        print(
            f"brain today    : {today.get('tick', 0)} ticks, "
            f"{today.get('notable', 0)} notables, "
            f"{today.get('spoken', 0)} spoken, "
            f"{today.get('action', 0)} actions"
        )
        if audit.get("last_state_note"):
            print(f"last note      : {audit['last_state_note']}")
        for m in audit.get("recent_spoken", []):
            print(f"  said         : {m}")
    else:
        print("brain today    : no audit log yet")

    tasks = _tasks(audit_db)
    if tasks:
        print("tasks          :")
        for t in tasks:
            print(f"  {t}")

    usage_file = _ROOT / "alfred_usage.json"
    if usage_file.exists():
        try:
            from datetime import datetime, timezone

            today = datetime.now(timezone.utc).date().isoformat()
            u = json.loads(usage_file.read_text()).get(today, {})
            if u:
                errs = ", ".join(
                    f"{k}:{v}" for k, v in u.get("errors", {}).items()
                )
                print(
                    f"gemini today   : {u.get('requests', 0)} requests, "
                    f"{u.get('input_tokens', 0) + u.get('output_tokens', 0)} "
                    f"tokens" + (f"  (errors: {errs})" if errs else "")
                )
        except Exception:  # noqa: BLE001
            pass

    print("ollama models  :")
    for line in _ollama_ps():
        print(f"  {line}")

    log = _ROOT / "logs" / "alfred.log"
    if log.exists():
        print(f"log            : {log}  ({log.stat().st_size // 1024} KB)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
