"""
python -m src.voice.enroll_voice

Teaches Alfred your voice, so the wake word only fires for you (see
src/voice/speaker_id.py for how). Records a handful of short takes of
you saying your wake phrase, turns each into a voiceprint, and
averages them into alfred_voiceprint.json in the project root -
gitignored, never leaves this machine, and isn't a recording of your
voice, just 192 numbers derived from it.

Say it the way you actually would when calling Alfred, not a flat
recitation - some natural variation between takes makes the average
more robust, not less.

    python -m src.voice.enroll_voice            # 5 takes, ~3s each
    python -m src.voice.enroll_voice --clips 8   # more takes
    python -m src.voice.enroll_voice --reset     # redo an existing enrollment
    python -m src.voice.enroll_voice --test      # check one take against
                                                  # what's enrolled, without
                                                  # changing it
"""

from __future__ import annotations

import argparse
import os
import time

import numpy as np

from src.voice import speaker_id

_SECONDS = 3.0


def _record(seconds: float) -> np.ndarray:
    import sounddevice as sd

    n = int(speaker_id.SAMPLE_RATE * seconds)
    audio = sd.rec(n, samplerate=speaker_id.SAMPLE_RATE, channels=1, dtype="int16")
    sd.wait()
    return audio.reshape(-1)


def _countdown_record(seconds: float, label: str) -> np.ndarray:
    print(f"{label} recording in", end=" ", flush=True)
    for n in (3, 2, 1):
        print(n, end=" ", flush=True)
        time.sleep(1)
    print("- go!")
    audio = _record(seconds)
    print("  ...got it.")
    return audio


def _enroll(clips: int, seconds: float) -> int:
    phrase = os.getenv("ALFRED_WAKE_PHRASE", "").strip() or "hey alfred"
    print(f'Say "{phrase}" (or however you actually wake Alfred), {clips} times.')
    print("Ctrl+C at any point stops without saving anything.\n")

    if speaker_id.ensure_model() is None:
        print("\n[enroll] can't get the voice model - see the message above.")
        return 1

    embeddings: list[np.ndarray] = []
    i = 1
    misses = 0
    while i <= clips:
        audio = _countdown_record(seconds, f"[{i}/{clips}]")
        emb = speaker_id.embed(audio)
        if emb is None:
            misses += 1
            if misses >= 3:
                print("\n[enroll] three takes in a row with nothing usable - stopping.")
                print("Check your mic (python -m src.doctor) and try again.")
                return 1
            print("  didn't catch enough speech there - let's redo that one.")
            continue
        embeddings.append(emb)
        misses = 0
        i += 1

    needed = max(2, clips // 2)
    if len(embeddings) < needed:
        print(f"\n[enroll] only {len(embeddings)}/{clips} takes were usable - not enrolling.")
        return 1

    path = speaker_id.save_voiceprint(embeddings)
    print(f"\n[enroll] saved {len(embeddings)} takes to {path}")
    print("Restart Alfred for it to take effect.")
    print(
        "python -m src.voice.enroll_voice --test   to check a take against it, "
        "or set ALFRED_SPEAKER_VERIFY_ENABLED=false in .env to switch it back off."
    )
    return 0


def _test(seconds: float, threshold: float) -> int:
    voiceprint = speaker_id.load_voiceprint()
    if voiceprint is None:
        print("[enroll] nothing enrolled yet - run without --test first.")
        return 1

    audio = _countdown_record(seconds, "[test]")
    emb = speaker_id.embed(audio)
    if emb is None:
        print("[enroll] couldn't hear enough speech in that take.")
        return 1

    score = speaker_id.cosine_similarity(voiceprint, emb)
    verdict = "MATCH" if score >= threshold else "no match"
    print(f"\nsimilarity: {score:.3f}   threshold: {threshold:.2f}   -> {verdict}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--clips", type=int, default=5, help="number of takes (default 5)")
    parser.add_argument("--seconds", type=float, default=_SECONDS, help="length of each take")
    parser.add_argument(
        "--threshold", type=float, default=speaker_id.DEFAULT_THRESHOLD,
        help="only used by --test",
    )
    parser.add_argument("--test", action="store_true", help="check one take, don't enroll")
    parser.add_argument("--reset", action="store_true", help="overwrite an existing enrollment")
    args = parser.parse_args()

    try:
        if args.test:
            return _test(args.seconds, args.threshold)

        if speaker_id.is_enrolled() and not args.reset:
            print(f"already enrolled ({speaker_id.VOICEPRINT_PATH}).")
            print("Re-run with --reset to redo it.")
            return 0

        return _enroll(args.clips, args.seconds)
    except KeyboardInterrupt:
        print("\n[enroll] stopped, nothing saved.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
