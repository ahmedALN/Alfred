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

    image_base64 = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    print(
        f"Image: {image_path}"
    )

    print(
        f"Bytes: {len(image_bytes)}"
    )

    print(
        f"Base64 characters: {len(image_base64)}"
    )

    print(
        "Creating Gemini client..."
    )

    client = genai.Client(
        api_key=settings.gemini_api_key
    )

    print(
        "Sending image request through Interactions API..."
    )

    started = time.perf_counter()

    interaction = client.interactions.create(
        model="gemini-3.7-flash",
        input=[
            {
                "type": "text",
                "text": (
                    "Inspect this exact screenshot carefully. "
                    "Only describe what is visibly present. "
                    "Identify the application window currently "
                    "in the foreground, any other visible "
                    "application windows, and what applications "
                    "are visible on the taskbar. "
                    "Pay particular attention to PowerShell "
                    "and Notepad if they are visible."
                ),
            },
            {
                "type": "image",
                "data": image_base64,
                "mime_type": "image/png",
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
