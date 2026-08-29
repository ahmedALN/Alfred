"""
python -m src.setup  -  get Alfred ready to run.

Checks Python deps, Ollama + models, builds the native helpers, and
creates a .env if you don't have one. Safe to run repeatedly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ENV = _ROOT / ".env"
_ENV_EXAMPLE = _ROOT / ".env.example"
_NATIVE = _ROOT / "src" / "windows" / "native"
_OLLAMA_MODELS = ("qwen3.5:4b", "nomic-embed-text")

_OK = "[ ok ]"
_WARN = "[warn]"
_TODO = "[todo]"


def _ask(prompt: str, default: bool = True) -> bool:
    if not sys.stdin.isatty():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    ans = input(prompt + suffix).strip().lower()
    if not ans:
        return default
    return ans.startswith("y")


def check_python_deps() -> None:
    missing = []
    for mod in ("google.genai", "sounddevice", "numpy", "psutil", "dotenv"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    for mod, pkg in (("openwakeword", "openwakeword"), ("onnxruntime", "onnxruntime")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    try:
        import win32api  # noqa: F401
    except ImportError:
        missing.append("pywin32")

    if missing:
        print(f"{_TODO} Python packages missing: {', '.join(missing)}")
        if _ask("    Install them now?"):
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r",
                 str(_ROOT / "requirements.txt")],
            )
    else:
        print(f"{_OK} Python dependencies")


def check_ollama() -> None:
    if shutil.which("ollama") is None:
        print(f"{_WARN} 'ollama' not found. Install from https://ollama.com "
              "if you want keyless local models (voice still needs Gemini).")
        return

    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4) as r:
            have = {m["name"] for m in json.loads(r.read()).get("models", [])}
    except Exception:  # noqa: BLE001
        print(f"{_WARN} Ollama installed but not running. Start it, then re-run.")
        return

    print(f"{_OK} Ollama is running")
    want = [m for m in _OLLAMA_MODELS if m not in have and m.split(":")[0] not in
            {h.split(":")[0] for h in have}]
    if want:
        print(f"{_TODO} missing models: {', '.join(want)}")
        if _ask("    Pull them now (a few GB)?"):
            for m in want:
                subprocess.run(["ollama", "pull", m])
    else:
        print(f"{_OK} local models present")


def build_native() -> None:
    if shutil.which("dotnet") is None:
        print(f"{_WARN} .NET SDK not found - the desktop-control helpers "
              "won't build. Install .NET 8+ from https://dotnet.microsoft.com")
        return

    projects = {
        "DesktopBridge": _NATIVE / "DesktopBridge" / "DesktopBridge.csproj",
        "ChildInputAgent": _NATIVE / "ChildInputAgent" / "ChildInputAgent.csproj",
    }
    exes_ok = list((_NATIVE).rglob("DesktopBridge.exe")) and \
        list((_NATIVE).rglob("ChildInputAgent.exe"))

    if exes_ok and not _ask("Native helpers already built. Rebuild?", default=False):
        print(f"{_OK} native helpers")
        return

    for name, proj in projects.items():
        if not proj.exists():
            print(f"{_WARN} {name}: {proj} missing")
            continue
        print(f"     building {name} ...")
        r = subprocess.run(
            ["dotnet", "build", str(proj), "-c", "Release", "-v", "q", "-nologo"],
            capture_output=True, text=True,
        )
        print(f"{_OK if r.returncode == 0 else _WARN} {name}")
        if r.returncode != 0:
            print(r.stdout[-800:])


def ensure_env() -> None:
    if _ENV.exists():
        print(f"{_OK} .env exists")
        return

    if not sys.stdin.isatty():
        shutil.copy(_ENV_EXAMPLE, _ENV)
        print(f"{_TODO} copied .env.example -> .env  (add your GEMINI_API_KEY)")
        return

    print("\nCreating .env ...")
    key = input("  Gemini API key (for voice; get one at aistudio.google.com): ").strip()
    provider = input("  Reasoning provider [ollama/gemini] (ollama): ").strip() or "ollama"

    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    text = text.replace("GEMINI_API_KEY=", f"GEMINI_API_KEY={key}")
    text = text.replace("ALFRED_AI_PROVIDER=gemini", f"ALFRED_AI_PROVIDER={provider}")
    if provider == "ollama":
        text = text.replace("#ALFRED_AI_CHAT_MODEL=qwen3.5:4b",
                            "ALFRED_AI_CHAT_MODEL=qwen3.5:4b")
        text = text.replace("#ALFRED_AI_VISION_MODEL=qwen3.5:4b",
                            "ALFRED_AI_VISION_MODEL=qwen3.5:4b")
        text = text.replace("#ALFRED_AI_EMBED_MODEL=nomic-embed-text",
                            "ALFRED_AI_EMBED_MODEL=nomic-embed-text")
    _ENV.write_text(text, encoding="utf-8")
    print(f"{_OK} wrote .env")


def offer_extras() -> None:
    if _ask("\nStart Alfred automatically at login?", default=False):
        subprocess.run([sys.executable, "-m", "src.autostart", "install"])

    if _ask('Record samples now to train a custom "Hey Alfred" wake word?',
            default=False):
        subprocess.run([sys.executable, "-m", "src.voice.train_wakeword", "record"])


def sync_env() -> int:
    """Add any keys present in .env.example but missing from .env."""
    if not _ENV.exists():
        return ensure_env() or 0

    def keys(path: Path) -> set[str]:
        return {
            line.split("=", 1)[0].strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        }

    have = keys(_ENV)
    added = []
    lines = _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    with open(_ENV, "a", encoding="utf-8") as fh:
        for line in lines:
            if "=" in line and not line.lstrip().startswith("#"):
                k = line.split("=", 1)[0].strip()
                if k not in have:
                    fh.write(f"\n{line}")
                    added.append(k)
    print(f"{_OK} .env synced" + (f" (+{', '.join(added)})" if added else " (nothing to add)"))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    if "--sync" in argv:
        return sync_env()

    print("Alfred setup")
    print("=" * 40)
    check_python_deps()
    ensure_env()
    sync_env()
    check_ollama()
    build_native()
    offer_extras()
    print("\nDone. Start Alfred with:  python -m src.main")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
