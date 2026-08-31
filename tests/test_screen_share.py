"""Sending someone their own screen, from their phone."""

from src.messaging.capture import MAX_SECONDS, ScreenShare


class _Sent:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def __call__(self, data, kind, caption=""):
        self.calls.append((kind, len(data), caption))
        return self.ok


def _share(png=b"PNG-DATA", sent=None, ffmpeg="ffmpeg"):
    sent = sent or _Sent()
    return ScreenShare(lambda: png, sent, ffmpeg=ffmpeg), sent


# --------------------------------------------------------------- picture


def test_the_picture_is_the_whole_reply():
    """Following a screenshot with a sentence saying a screenshot was
    sent is noise - it is right there."""
    share, sent = _share()

    assert share.picture() == ""
    kind, size, _caption = sent.calls[0]
    assert (kind, size) == ("image", 8)


def test_it_says_when_it_was_taken():
    """A picture of a screen looks the same whether it was captured now
    or an hour ago, and "is this current?" is not a question anybody
    should have to ask twice."""
    import re

    share, sent = _share()
    share.picture()

    assert re.search(r"Taken at \d\d:\d\d:\d\d", sent.calls[0][2])


def test_a_caption_of_your_own_wins():
    share, sent = _share()
    share.picture("here is the error")

    assert sent.calls[0][2] == "here is the error"


def test_a_screen_it_cannot_grab_is_said_out_loud():
    share = ScreenShare(
        lambda: (_ for _ in ()).throw(RuntimeError("no session")),
        _Sent(),
    )

    assert "no session" in share.picture()


def test_an_empty_grab_is_not_sent_as_a_picture():
    share, sent = _share(png=b"")

    assert share.picture()
    assert sent.calls == []


def test_a_picture_that_will_not_send_says_so_rather_than_going_quiet():
    share, sent = _share(sent=_Sent(ok=False))

    assert "couldn't send" in share.picture()


# ------------------------------------------------------------------ clip


def test_without_ffmpeg_it_offers_what_it_can_still_do():
    share, _ = _share(ffmpeg=None)
    answer = share.clip(5)

    assert "screenshot" in answer and "ffmpeg" in answer
    assert not share.can_record


def test_a_recording_is_capped_at_something_a_phone_will_accept():
    lengths = []

    class _Recording(ScreenShare):
        def _record(self, out, seconds):
            lengths.append(seconds)
            out.write_bytes(b"MP4")

    share = _Recording(lambda: b"", _Sent(), ffmpeg="ffmpeg")
    share.clip(9999)
    share.clip(0)

    assert lengths[0] == MAX_SECONDS
    assert lengths[1] >= 1


def test_the_clip_is_sent_as_a_video_not_a_file():
    sent = _Sent()

    class _Recording(ScreenShare):
        def _record(self, out, seconds):
            out.write_bytes(b"MP4-DATA")

    assert _Recording(lambda: b"", sent, ffmpeg="ffmpeg").clip(3) == ""
    assert sent.calls[0][0] == "video"


def test_a_recording_that_fails_says_why():
    class _Broken(ScreenShare):
        def _record(self, out, seconds):
            raise RuntimeError("gdigrab not available")

    assert "gdigrab" in _Broken(lambda: b"", _Sent(), ffmpeg="ffmpeg").clip(3)


def test_an_empty_recording_is_not_sent():
    sent = _Sent()

    class _Empty(ScreenShare):
        def _record(self, out, seconds):
            out.write_bytes(b"")

    assert "empty" in _Empty(lambda: b"", sent, ffmpeg="ffmpeg").clip(3)
    assert sent.calls == []
