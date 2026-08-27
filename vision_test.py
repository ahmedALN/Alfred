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

    print(f"Image: {image_path}")
    print(f"Bytes: {len(image_bytes)}")
    print("Creating Gemini client...")

    client = genai.Client(
        api_key=settings.gemini_api_key,
    )

    print(
        "Sending Computer Use interaction "
        "to gemini-3.7-flash..."
    )

    started = time.perf_counter()

    interaction = client.interactions.create(
        model="gemini-3.7-flash",
        input=[
            {
                "type": "text",
                "text": (
                    "Inspect this exact Windows desktop screenshot. "
                    "Do not perform any action. "
                    "Identify the foreground application and "
                    "any clearly visible background application "
                    "windows."
                ),
            },
            {
                "type": "image",
                "data": image_base64,
                "mime_type": "image/png",
            },
        ],
        tools=[
            {
                "type": "computer_use",
                "environment": "desktop",
                "enable_prompt_injection_detection": True,
            }
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
    print("INTERACTION")
    print("===========")
    print(interaction)

    print()
    print("OUTPUT TEXT")
    print("===========")

    output_text = getattr(
        interaction,
        "output_text",
        None,
    )

    print(output_text)

    print()
    print("OUTPUT")
    print("======")

    output = getattr(
        interaction,
        "output",
        None,
    )

    if output is None:
        print("No output attribute.")
    else:
        for index, item in enumerate(
            output,
            start=1,
        ):
            print(
                f"[{index}] {item!r}"
            )


if __name__ == "__main__":
    main()
