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
        "Testing Interactions API with screenshot..."
    )

    print(
        f"Image: {image_path}"
    )

    print(
        f"Image bytes: {len(image_bytes)}"
    )

    image_base64 = base64.b64encode(
        image_bytes
    ).decode("ascii")

    print(
        f"Base64 characters: {len(image_base64)}"
    )

    client = genai.Client(
        api_key=settings.gemini_api_key
    )

    prompt = (
        "Inspect this exact screenshot carefully. "
        "Only describe what is visibly present. "
        "Identify the application in the foreground, "
        "any other visible application windows, and "
        "what applications are visible on the taskbar. "
        "Pay particular attention to PowerShell and "
        "Notepad if they are visible. "
        "Do not infer hidden windows or previous state."
    )

    print(
        "Sending image to gemini-3.5-flash-lite..."
    )

    started = time.perf_counter()

    interaction = client.interactions.create(
        model="gemini-3.5-flash-lite",
        input=[
            {
                "type": "text",
                "text": prompt,
            },
            {
                "type": "image",
                "data": image_base64,
                "mime_type": "image/png",
            },
        ],
        generation_config={
            "thinking_level": "minimal",
        },
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
