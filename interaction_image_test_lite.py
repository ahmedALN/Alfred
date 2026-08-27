from __future__ import annotations

import base64
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

    base64_image = base64.b64encode(
        image_bytes
    ).decode("ascii")

    print(
        f"Image: {image_path}"
    )

    print(
        f"Bytes: {len(image_bytes)}"
    )

    print(
        f"Base64 characters: {len(base64_image)}"
    )

    print(
        "Creating Gemini client..."
    )

    client = genai.Client(
        api_key=settings.gemini_api_key
    )

    print(
        "Sending image request through "
        "Interactions API using gemini-3.5-flash-lite..."
    )

    interaction = client.interactions.create(
        model="gemini-3.5-flash-lite",
        input=[
            {
                "type": "text",
                "text": (
                    "Inspect this exact screenshot carefully. "
                    "Only report things actually visible in it. "
                    "Identify the foreground application, "
                    "other visible application windows, and "
                    "what applications are visible on the taskbar. "
                    "Pay particular attention to PowerShell "
                    "and Notepad. Do not infer hidden windows."
                ),
            },
            {
                "type": "image",
                "mime_type": "image/png",
                "data": base64_image,
            },
        ],
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
