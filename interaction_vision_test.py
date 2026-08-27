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

    print(
        f"Image: {image_path}"
    )

    print(
        f"PNG bytes: {len(image_bytes)}"
    )

    encoded_image = base64.b64encode(
        image_bytes
    ).decode("ascii")

    print(
        f"Base64 characters: {len(encoded_image)}"
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

    interaction = (
        client.interactions.create(
            model="gemini-3.5-flash-lite",
            input=[
                {
                    "type": "image",
                    "data": encoded_image,
                    "mime_type": "image/png",
                },
                {
                    "type": "text",
                    "text": (
                        "Inspect this exact screenshot carefully. "
                        "Do not guess or rely on previous context. "
                        "Describe only what is visibly present. "
                        "Identify the foreground application, "
                        "any other visible application windows, "
                        "and the applications visible on the taskbar. "
                        "Pay particular attention to whether "
                        "PowerShell and Notepad are visible. "
                        "State their approximate positions if "
                        "they are visible."
                    ),
                },
            ],
        )
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
