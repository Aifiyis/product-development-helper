import base64
import json
import uuid
import urllib.error
import urllib.request
from pathlib import Path

from flask import current_app, url_for


OPENAI_SIZE_MAP = {
    "1:1": "1024x1024",
    "4:5": "1024x1536",
    "9:16": "1024x1536",
    "16:9": "1536x1024",
}

GEMINI_RATIO_MAP = {
    "1:1": "1:1",
    "4:5": "3:4",
    "9:16": "9:16",
    "16:9": "16:9",
}


class ImageGenerationError(RuntimeError):
    pass


def generate_ad_image(prompt, aspect_ratio, provider=None):
    provider = (provider or current_app.config.get("IMAGE_PROVIDER") or "openai").lower()
    if provider == "gemini":
        image_bytes = _generate_with_gemini(prompt, aspect_ratio)
    elif provider == "openai":
        image_bytes = _generate_with_openai(prompt, aspect_ratio)
    else:
        raise ImageGenerationError(f"Unsupported image provider: {provider}")

    relative_path = _save_generated_image(image_bytes, provider)
    return {
        "provider": provider,
        "image_url": url_for("static", filename=relative_path, _external=False),
    }


def _generate_with_openai(prompt, aspect_ratio):
    api_key = current_app.config.get("OPENAI_API_KEY")
    if not api_key:
        raise ImageGenerationError("OPENAI_API_KEY is not configured.")

    payload = {
        "model": current_app.config.get("OPENAI_IMAGE_MODEL") or "gpt-image-1",
        "prompt": prompt,
        "size": OPENAI_SIZE_MAP.get(aspect_ratio, "1024x1024"),
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise ImageGenerationError(f"OpenAI image generation failed: {detail}") from exc

    item = data.get("data", [{}])[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=90) as image_response:
            return image_response.read()
    raise ImageGenerationError("OpenAI response did not include image data.")


def _generate_with_gemini(prompt, aspect_ratio):
    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        raise ImageGenerationError("GEMINI_API_KEY is not configured.")

    model = current_app.config.get("GEMINI_IMAGE_MODEL") or "imagen-4.0-generate-001"
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict?key={api_key}"
    payload = {
        "instances": [{"prompt": prompt}],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": GEMINI_RATIO_MAP.get(aspect_ratio, "1:1"),
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise ImageGenerationError(f"Gemini image generation failed: {detail}") from exc

    prediction = data.get("predictions", [{}])[0]
    encoded = prediction.get("bytesBase64Encoded") or prediction.get("image", {}).get("bytesBase64Encoded")
    if not encoded:
        raise ImageGenerationError("Gemini response did not include image data.")
    return base64.b64decode(encoded)


def _save_generated_image(image_bytes, provider):
    target_dir = Path(current_app.static_folder) / "generated"
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{provider}_{uuid.uuid4().hex}.png"
    (target_dir / filename).write_bytes(image_bytes)
    return f"generated/{filename}"
