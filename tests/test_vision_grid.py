import io

from src.ai.vision import annotate_grid, screenshot_prompt


def _blank_png(w=400, h=300):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_annotate_grid_returns_valid_png_of_same_size():
    from PIL import Image

    original = _blank_png(640, 480)
    gridded = annotate_grid(original, spacing=100)

    assert gridded != original
    img = Image.open(io.BytesIO(gridded))
    assert img.size == (640, 480)


def test_annotate_grid_survives_garbage_input():
    assert annotate_grid(b"not a png") == b"not a png"


def test_prompt_mentions_grid_only_when_gridded():
    assert "grid" not in screenshot_prompt(100, 100, gridded=False).lower()
    p = screenshot_prompt(100, 100, gridded=True)
    assert "grid" in p.lower() and "pixel coordinates" in p.lower()


def test_prompt_forbids_json_output():
    p = screenshot_prompt(100, 100, gridded=True)
    assert "do not return json" in p.lower()
    assert "center at (x, y)" in p.lower()
