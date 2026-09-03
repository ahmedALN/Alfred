"""Speaker verification for the wake word - "only respond to 'alfred'
through my own voice alone".

None of this can prove real-world accuracy from here (that needs a
real microphone and the user's real voice, like the VAD timing and
half-duplex tests elsewhere in this suite can't either) - what's
tested is the math that has to be right regardless of whose voice it
is (cosine similarity, averaging, threshold semantics, voiceprint
storage), and that the real feature-extraction pipeline
(kaldi_native_fbank, actually installed, not mocked) runs end to end
against a stub model without ever touching the network or the real
~25 MB ONNX file.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.voice import speaker_id

# ====================================================================
# cosine_similarity
# ====================================================================


def test_identical_vectors_are_a_perfect_match():
    v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert speaker_id.cosine_similarity(v, v) == pytest.approx(1.0)


def test_opposite_vectors_are_as_far_apart_as_possible():
    v = np.array([1.0, 0.0], dtype=np.float32)
    assert speaker_id.cosine_similarity(v, -v) == pytest.approx(-1.0)


def test_orthogonal_vectors_score_zero():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert speaker_id.cosine_similarity(a, b) == pytest.approx(0.0)


def test_scale_does_not_matter_only_direction():
    a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert speaker_id.cosine_similarity(a, a * 50.0) == pytest.approx(1.0)


def test_a_zero_vector_does_not_crash_it():
    a = np.zeros(4, dtype=np.float32)
    b = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
    assert speaker_id.cosine_similarity(a, b) == 0.0


# ====================================================================
# Voiceprint storage
# ====================================================================


@pytest.fixture
def voiceprint_path(tmp_path, monkeypatch):
    path = tmp_path / "alfred_voiceprint.json"
    monkeypatch.setattr(speaker_id, "VOICEPRINT_PATH", path)
    return path


def test_nothing_enrolled_yet(voiceprint_path):
    assert speaker_id.is_enrolled() is False
    assert speaker_id.load_voiceprint() is None


def test_save_then_load_round_trips(voiceprint_path):
    emb = np.random.default_rng(0).standard_normal(speaker_id.EMBED_DIM).astype(
        np.float32
    )
    speaker_id.save_voiceprint([emb])

    assert speaker_id.is_enrolled() is True
    loaded = speaker_id.load_voiceprint()
    assert loaded is not None
    assert loaded.shape == (speaker_id.EMBED_DIM,)
    # Saved normalized, so it should point the same way as the input.
    assert speaker_id.cosine_similarity(loaded, emb) == pytest.approx(1.0, abs=1e-5)


def test_averaging_normalizes_first_so_loudness_does_not_dominate(voiceprint_path):
    rng = np.random.default_rng(1)
    direction = rng.standard_normal(speaker_id.EMBED_DIM).astype(np.float32)
    quiet = direction * 0.1
    loud = direction * 50.0  # same direction, wildly different magnitude

    speaker_id.save_voiceprint([quiet, loud])
    loaded = speaker_id.load_voiceprint()

    # Both takes point the same way, so the average should too, almost
    # exactly - despite one being 500x the magnitude of the other.
    assert speaker_id.cosine_similarity(loaded, direction) == pytest.approx(
        1.0, abs=1e-4
    )


def test_saving_with_nothing_usable_raises_rather_than_writing_junk(voiceprint_path):
    with pytest.raises(ValueError):
        speaker_id.save_voiceprint([])


def test_a_corrupt_voiceprint_file_is_ignored_not_a_crash(voiceprint_path):
    voiceprint_path.write_text("not json at all", encoding="utf-8")
    assert speaker_id.load_voiceprint() is None


def test_a_voiceprint_of_the_wrong_shape_is_ignored(voiceprint_path):
    import json

    voiceprint_path.write_text(
        json.dumps({"embedding": [1.0, 2.0, 3.0]}), encoding="utf-8"
    )
    assert speaker_id.load_voiceprint() is None


# ====================================================================
# matches() - the None/True/False contract the wake listener relies on
# ====================================================================


def test_matches_is_none_when_nothing_is_enrolled(voiceprint_path):
    audio = np.zeros(speaker_id.SAMPLE_RATE, dtype=np.int16)
    assert speaker_id.matches(audio) is None


def test_matches_is_true_over_threshold(voiceprint_path, monkeypatch):
    voiceprint = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    speaker_id.save_voiceprint([np.pad(voiceprint, (0, speaker_id.EMBED_DIM - 3))])
    candidate = speaker_id.load_voiceprint()  # same direction: similarity 1.0

    monkeypatch.setattr(speaker_id, "embed", lambda samples: candidate)
    assert speaker_id.matches(np.zeros(10, dtype=np.int16), threshold=0.9) is True


def test_matches_is_false_under_threshold(voiceprint_path, monkeypatch):
    rng = np.random.default_rng(2)
    enrolled = rng.standard_normal(speaker_id.EMBED_DIM).astype(np.float32)
    speaker_id.save_voiceprint([enrolled])

    # Deliberately orthogonal-ish candidate: negate every other dim.
    off = enrolled.copy()
    off[::2] *= -1
    monkeypatch.setattr(speaker_id, "embed", lambda samples: off)

    assert speaker_id.matches(np.zeros(10, dtype=np.int16), threshold=0.95) is False


def test_matches_is_none_when_the_audio_could_not_be_embedded(
    voiceprint_path, monkeypatch
):
    speaker_id.save_voiceprint(
        [np.random.default_rng(3).standard_normal(speaker_id.EMBED_DIM).astype(np.float32)]
    )
    monkeypatch.setattr(speaker_id, "embed", lambda samples: None)

    assert speaker_id.matches(np.zeros(10, dtype=np.int16)) is None


# ====================================================================
# _trim_silence
# ====================================================================


def test_trimming_pure_silence_does_not_crash():
    silence = np.zeros(speaker_id.SAMPLE_RATE, dtype=np.int16)
    out = speaker_id._trim_silence(silence)
    assert out.size <= silence.size


def test_trimming_isolates_a_loud_burst_in_silence():
    sr = speaker_id.SAMPLE_RATE
    samples = np.zeros(sr * 3, dtype=np.int16)  # 3s silence
    burst_start, burst_end = sr, sr + sr // 2  # 0.5s loud burst in the middle
    samples[burst_start:burst_end] = 8000

    trimmed = speaker_id._trim_silence(samples, pad_ms=50.0)

    assert trimmed.size < samples.size
    assert np.abs(trimmed).max() >= 8000


def test_an_empty_array_is_handled():
    assert speaker_id._trim_silence(np.array([], dtype=np.int16)).size == 0


# ====================================================================
# embed() - the real fbank pipeline, a stub model
# ====================================================================


class _FakeInput:
    name = "feats"


class _FakeSession:
    """Not a real speaker model - just proves the plumbing (trim -> real
    kaldi_native_fbank -> CMN -> session.run -> 192-dim vector) actually
    runs, without a network call or the real ~25 MB file."""

    def get_inputs(self):
        return [_FakeInput()]

    def run(self, output_names, feed):
        feats = feed["feats"]
        assert feats.ndim == 3 and feats.shape[0] == 1 and feats.shape[2] == 80
        val = float(feats.sum() % 1.0) + 0.01  # never exactly zero
        return [np.full((1, speaker_id.EMBED_DIM), val, dtype=np.float32)]


def _tone(seconds: float, freq: float = 150.0, amp: float = 5000.0) -> np.ndarray:
    sr = speaker_id.SAMPLE_RATE
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def test_embed_runs_the_real_fbank_pipeline_end_to_end(monkeypatch):
    monkeypatch.setattr(speaker_id, "_load_session", lambda: _FakeSession())

    out = speaker_id.embed(_tone(1.5))

    assert out is not None
    assert out.shape == (speaker_id.EMBED_DIM,)
    assert np.isfinite(out).all()


def test_embed_is_none_for_audio_too_short_to_say_anything_in(monkeypatch):
    monkeypatch.setattr(speaker_id, "_load_session", lambda: _FakeSession())
    out = speaker_id.embed(_tone(0.1))  # 100ms, under the 250ms floor
    assert out is None


def test_embed_is_none_for_silence(monkeypatch):
    monkeypatch.setattr(speaker_id, "_load_session", lambda: _FakeSession())
    out = speaker_id.embed(np.zeros(speaker_id.SAMPLE_RATE, dtype=np.int16))
    assert out is None


def test_embed_never_raises_when_the_model_is_unavailable(monkeypatch):
    monkeypatch.setattr(speaker_id, "_load_session", lambda: None)
    assert speaker_id.embed(_tone(1.5)) is None


def test_embed_never_raises_when_the_model_itself_errors(monkeypatch):
    class _Boom:
        def get_inputs(self):
            return [_FakeInput()]

        def run(self, *a, **k):
            raise RuntimeError("onnxruntime blew up")

    monkeypatch.setattr(speaker_id, "_load_session", lambda: _Boom())
    assert speaker_id.embed(_tone(1.5)) is None


def test_a_short_audio_check_never_touches_the_network(monkeypatch):
    """embed() should bail out on too-short audio before ever trying
    to load/download the model - a bug here would mean every stray
    noise attempts a network fetch."""
    calls = []
    monkeypatch.setattr(
        speaker_id, "_load_session", lambda: calls.append(1) or _FakeSession()
    )
    speaker_id.embed(_tone(0.05))
    assert calls == []


# ====================================================================
# The default threshold and dimension are sane, not placeholders
# ====================================================================


def test_default_threshold_is_a_plausible_cosine_cutoff():
    assert 0.0 < speaker_id.DEFAULT_THRESHOLD < 1.0


def test_embedding_dimension_matches_the_real_model():
    """Ground-truthed against the model's own ONNX graph (output
    tensor 'embs': [batch, 192]), not assumed."""
    assert speaker_id.EMBED_DIM == 192
