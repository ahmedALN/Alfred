from __future__ import annotations

import base64
import time
from pathlib import Path

from google import genai

from src.config import load_settings


def main() -> None:
    settings = load_settings()

    image_path = (
        Path(__file__).resolve().parent
        / "debug-child-desktop.png"
    )

    if not image_path.exists():
        raise FileNotFoundError(
            f"Screenshot not found: {image_path}"
        )

    image_bytes = image_path.read_bytes()

    if not image_bytes:
        raise RuntimeError(
            "Screenshot file is empty."
        )

    image_base64 = (
        base64.b64encode(
            image_bytes
        ).decode("utf-8")
    )

    print(
        f"Image: {image_path}"
    )

    print(
        f"Bytes: {len(image_bytes)}"
    )

    print(
        "Creating Gemini client..."
    )

    client = genai.Client(
        api_key=settings.gemini_api_key
    )

    print(
        "Sending Interactions API image request..."
    )

    started = time.perf_counter()

    interaction = client.interactions.create(
        model="gemini-3.5-flash-lite",
        input=[
            {
                "type": "image",
                "data": image_base64,
                "mime_type": "image/png",
            },
            {
                "type": "text",
                "text": (
                    "Look only at the screenshot supplied with "
                    "this request. "
                    "Describe exactly what is visible. "
                    "State the foreground application window "
                    "first. "
                    "Then state any other visible application "
                    "windows. "
                    "Then state which application icons are "
                    "visible on the taskbar. "
                    "Do not infer hidden windows, previous "
                    "state, or applications that are not "
                    "visibly present."
                ),
            },
        ],
        generation_config={
            "thinking_level": "minimal",
        },
        timeout=30,
    )

    elapsed = (
        time.perf_counter()
        - started
    )

    print(
        f"Response received in {elapsed:.2f} seconds."
    )

    print()
    print("MODEL RESPONSE")
    print("==============")

    print(
        interaction.output_text
    )


if __name__ == "__main__":
    main()
