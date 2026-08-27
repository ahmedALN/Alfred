from __future__ import annotations

import base64
import json
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
        f"Image bytes: {len(image_bytes)}"
    )

    image_base64 = base64.b64encode(
        image_bytes
    ).decode("utf-8")

    client = genai.Client(
        api_key=settings.gemini_api_key
    )

    response_schema = {
        "type": "object",
        "properties": {
            "screen_width": {
                "type": "integer"
            },
            "screen_height": {
                "type": "integer"
            },
            "windows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "application": {
                            "type": "string"
                        },
                        "title": {
                            "type": "string"
                        },
                        "x": {
                            "type": "integer"
                        },
                        "y": {
                            "type": "integer"
                        },
                        "width": {
                            "type": "integer"
                        },
                        "height": {
                            "type": "integer"
                        },
                        "foreground": {
                            "type": "boolean"
                        }
                    },
                    "required": [
                        "application",
                        "title",
                        "x",
                        "y",
                        "width",
                        "height",
                        "foreground"
                    ]
                }
            }
        },
        "required": [
            "screen_width",
            "screen_height",
            "windows"
        ]
    }

    prompt = (
        "Analyze this exact Windows desktop screenshot. "
        "This image is the complete child-session desktop. "
        "Return only applications whose windows are visibly "
        "present in the screenshot. "
        "Do not infer hidden, minimized, or previously open "
        "applications. "
        "Do not invent windows. "
        "For every visibly open application window, return "
        "its approximate bounding box in pixels using this "
        "coordinate system: x=distance from the left edge, "
        "y=distance from the top edge, width=window width, "
        "height=window height. "
        "Also identify which visible window is currently "
        "in the foreground. "
        "The desktop resolution is expected to be "
        "1264 by 761 pixels. "
        "If PowerShell or Notepad are visibly present, "
        "identify them explicitly."
    )

    print(
        "Sending screenshot to Gemini Interactions API..."
    )

    started = time.perf_counter()

    interaction = client.interactions.create(
        model="gemini-3.7-flash",
        input=[
            {
                "type": "image",
                "data": image_base64,
                "mime_type": "image/png",
            },
            {
                "type": "text",
                "text": prompt,
            },
        ],
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": response_schema,
        },
    )

    elapsed = (
        time.perf_counter()
        - started
    )

    print(
        f"Response received in {elapsed:.2f} seconds."
    )

    output_text = interaction.output_text

    if not output_text:
        raise RuntimeError(
            "Interactions API returned no output text."
        )

    print()
    print(
        "MODEL OUTPUT"
    )
    print(
        "============"
    )

    print(
        output_text
    )

    print()
    print(
        "PARSED RESULT"
    )
    print(
        "============="
    )

    parsed = json.loads(
        output_text
    )

    print(
        json.dumps(
            parsed,
            indent=2
        )
    )

    output_path = (
        Path(__file__).resolve().parent
        / "debug-bounding-boxes.json"
    )

    output_path.write_text(
        json.dumps(
            parsed,
            indent=2
        ),
        encoding="utf-8"
    )

    print()
    print(
        f"Saved result to: {output_path}"
    )


if __name__ == "__main__":
    main()
