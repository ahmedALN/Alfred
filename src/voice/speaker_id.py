"""
Speaker verification for the wake word: once you've enrolled
(``python -m src.voice.enroll_voice``), the wake word only fires for
your voice - a housemate, a TV ad, or a podcast saying "hey alfred"
does not wake Alfred.

Model: WeSpeaker's ECAPA-TDNN x-vector extractor
(``Wespeaker/wespeaker-ecapa-tdnn512-LM`` on HuggingFace, CC-BY-4.0,
trained on VoxCeleb2), ~25 MB, run locally through onnxruntime -
nothing about your voice ever leaves this machine. It turns a few
seconds of 16 kHz speech into a 192-number "voiceprint"; two
voiceprints from the same person land close together by cosine
similarity, different people's land further apart.

The preprocessing below was not guessed - it was checked against the
model's own ONNX graph (input tensor ``feats``: [batch, time, 80]),
against WeSpeaker's training feature extraction
(``wespeaker/dataset/processor.py``: 25 ms / 10 ms Hamming-window,
80-bin mel filterbank, log energies, no energy term) and against its
C++ inference reference (``runtime/core/speaker/speaker_engine.cc``:
per-utterance mean subtraction before the model, dither disabled at
inference even though training used it as augmentation) - all on
https://github.com/wenet-e2e/wespeaker. The one thing that reference
inference engine does that this doesn't is silence trimming - it has
none, because it's handed one clean utterance at a time. We hand it a
few seconds of rolling capture buffer around a roughly one-second
phrase, so `_trim_silence` below earns its keep here in a way it
doesn't for them.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent.parent
_MODEL_DIR = _ROOT / "models" / "speaker"
_MODEL_NAME = "voxceleb_ECAPA512_LM.onnx"
_MODEL_PATH = _MODEL_DIR / _MODEL_NAME
_MODEL_URL = (
    "https://huggingface.co/Wespeaker/wespeaker-ecapa-tdnn512-LM/"
    f"resolve/main/{_MODEL_NAME}"
)

VOICEPRINT_PATH = _ROOT / "alfred_voiceprint.json"

SAMPLE_RATE = 16_000
EMBED_DIM = 192
# A starting point, not a measured EER threshold for this exact export -
# raise it (fewer false accepts, more "didn't catch that, try again") if
# someone else's voice is getting through; lower it if your own voice
# keeps getting rejected. `python -m src.voice.enroll_voice --test`
# prints the raw similarity score so you can see where you actually
# land before moving it.
DEFAULT_THRESHOLD = 0.42

_session = None  # cached onnxruntime.InferenceSession


def ensure_model() -> Path | None:
    """Downloads the ~25 MB ONNX model on first use. Returns its path,
    or None if it isn't there and couldn't be fetched."""
    if _MODEL_PATH.exists():
        return _MODEL_PATH

    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print("[SpeakerID] downloading voice model (~25 MB, one-time) ...")
    try:
        with urllib.request.urlopen(_MODEL_URL, timeout=120) as resp:
            data = resp.read()
        _MODEL_PATH.write_bytes(data)
    except Exception as exc:  # noqa: BLE001
        print(f"[SpeakerID] model download failed: {exc}")
        print(f"Get it manually from {_MODEL_URL} and save it to {_MODEL_PATH}")
        return None
    print(f"[SpeakerID] installed to {_MODEL_PATH}")
    return _MODEL_PATH


def _load_session():
    """Cached onnxruntime session, or None if it can't be loaded. A
    module-level seam so tests can stub this out without a real model
    file or a network call."""
    global _session
    if _session is not None:
        return _session

    path = ensure_model()
    if path is None:
        return None
    try:
        import onnxruntime as ort

        _session = ort.InferenceSession(
            str(path), providers=["CPUExecutionProvider"]
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[SpeakerID] could not load voice model: {exc}")
        return None
    return _session


def _fbank_opts():
    import kaldi_native_fbank as knf

    opts = knf.FbankOptions()
    opts.frame_opts.samp_freq = SAMPLE_RATE
    opts.frame_opts.frame_length_ms = 25
    opts.frame_opts.frame_shift_ms = 10
    # Training used dither=1.0 as augmentation; the reference C++
    # inference engine runs with it disabled for a reproducible
    # embedding, which is what we want when comparing two takes.
    opts.frame_opts.dither = 0.0
    opts.frame_opts.window_type = "hamming"
    opts.frame_opts.remove_dc_offset = True
    opts.frame_opts.snip_edges = True
    opts.mel_opts.num_bins = 80
    opts.use_energy = False
    opts.use_log_fbank = True
    return opts


def _trim_silence(
    samples: np.ndarray, floor_ratio: float = 0.03, pad_ms: float = 150.0
) -> np.ndarray:
    """A rolling capture buffer is mostly silence around a ~1s phrase;
    feeding all of it in dilutes the per-utterance mean-normalisation
    the model expects a clean utterance to have. Cheap energy-gate
    trim, in 30ms frames, padded so we don't clip the phrase itself."""
    if samples.size == 0:
        return samples

    win = max(1, int(SAMPLE_RATE * 0.03))
    frame_count = samples.size // win
    if frame_count < 2:
        return samples

    frames = samples[: frame_count * win].reshape(frame_count, win)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1))
    peak = rms.max()
    if peak <= 0:
        # True digital silence, not just quiet - there is nothing here
        # to verify against, as opposed to "too short a clip to judge"
        # below, where we deliberately leave the audio alone.
        return samples[:0]

    loud = np.where(rms >= peak * floor_ratio)[0]
    if loud.size == 0:
        return samples

    pad = int(SAMPLE_RATE * pad_ms / 1000.0)
    start = max(0, loud[0] * win - pad)
    end = min(samples.size, (loud[-1] + 1) * win + pad)
    return samples[start:end]


def embed(samples: np.ndarray) -> np.ndarray | None:
    """int16 mono @16kHz -> a 192-dim voiceprint, or None if this
    couldn't be computed - too little audio to say anything in, or a
    missing model/dependency. Never raises."""
    samples = np.asarray(samples, dtype=np.int16)
    trimmed = _trim_silence(samples)
    if trimmed.size < SAMPLE_RATE // 4:  # under 250ms of actual sound
        return None

    sess = _load_session()
    if sess is None:
        return None

    try:
        import kaldi_native_fbank as knf
    except Exception as exc:  # noqa: BLE001
        print(f"[SpeakerID] kaldi_native_fbank unavailable: {exc}")
        return None

    try:
        fbank = knf.OnlineFbank(_fbank_opts())
        fbank.accept_waveform(SAMPLE_RATE, trimmed.astype(np.float32))
        fbank.input_finished()
        n = fbank.num_frames_ready
        if n < 10:
            return None
        feats = np.stack([fbank.get_frame(i) for i in range(n)]).astype(
            np.float32
        )
        feats -= feats.mean(axis=0, keepdims=True)  # per-utterance CMN
        input_name = sess.get_inputs()[0].name
        out = sess.run(None, {input_name: feats[None, :, :]})[0][0]
    except Exception as exc:  # noqa: BLE001
        print(f"[SpeakerID] embedding extraction failed: {exc}")
        return None

    return out.astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


# ==================================================================
# Voiceprint storage
# ==================================================================


def is_enrolled() -> bool:
    return VOICEPRINT_PATH.exists()


def load_voiceprint() -> np.ndarray | None:
    if not VOICEPRINT_PATH.exists():
        return None
    try:
        data = json.loads(VOICEPRINT_PATH.read_text(encoding="utf-8"))
        vec = np.array(data["embedding"], dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        print(f"[SpeakerID] voiceprint file unreadable: {exc}")
        return None
    if vec.shape != (EMBED_DIM,):
        print(f"[SpeakerID] voiceprint has the wrong shape {vec.shape}; ignoring.")
        return None
    return vec


def save_voiceprint(embeddings: list[np.ndarray]) -> Path:
    """Averages a few enrollment takes into one voiceprint and writes
    it. Each embedding is L2-normalized before averaging, so one
    unusually loud or quiet take doesn't dominate the result - only
    its direction counts, same as the cosine comparison later does."""
    normed = []
    for e in embeddings:
        n = float(np.linalg.norm(e))
        if n > 0:
            normed.append(e / n)
    if not normed:
        raise ValueError("no usable embeddings to save")

    mean = np.mean(normed, axis=0)
    mean = mean / np.linalg.norm(mean)

    VOICEPRINT_PATH.write_text(
        json.dumps(
            {
                "embedding": mean.tolist(),
                "samples": len(normed),
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return VOICEPRINT_PATH


def matches(samples: np.ndarray, threshold: float = DEFAULT_THRESHOLD) -> bool | None:
    """True/False once the audio has actually been compared against
    the enrolled voiceprint. None means it couldn't be checked at all
    - nothing enrolled, or the check itself failed - and callers
    decide what "couldn't check" should mean for them; the wake
    listener treats it as a mismatch, since the entire point of
    enrolling is that only a positive match should ever fire."""
    voiceprint = load_voiceprint()
    if voiceprint is None:
        return None
    candidate = embed(samples)
    if candidate is None:
        return None
    return cosine_similarity(voiceprint, candidate) >= threshold
