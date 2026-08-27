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
            "Screenshot is empty."
        )

    print(
        f"Image: {image_path}"
    )

    print(
        f"Bytes: {len(image_bytes)}"
    )

    image_b64 = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    print(
        "Creating Gemini client..."
    )

    client = genai.Client(
        api_key=settings.gemini_api_key
    )

    print(
        "Sending Interactions API request..."
    )

    started = time.perf_counter()

    interaction = client.interactions.create(
        model="gemini-3.7-flash",
        input=[
            {
                "type": "image",
                "mime_type": "image/png",
                "data": image_b64,
            },
            {
                "type": "text",
                "text": (
                    "Inspect this exact screenshot. "
                    "Identify the application currently in "
                    "the foreground. Identify any other "
                    "application windows that are visibly open. "
                    "Also describe what application icons or "
                    "windows are visible on the taskbar. "
                    "Only report what you can actually see "
                    "in this image. Do not infer hidden state."
                ),
            },
        ],
    )

    elapsed = (
        time.perf_counter()
        - started
    )

    print(
        f"Response received in {elapsed:.2f} seconds."
    )

    print()
    print(
        "MODEL RESPONSE"
    )
    print(
        "=============="
    )

    print(
        interaction.output_text
    )


if __name__ == "__main__":
    main()
