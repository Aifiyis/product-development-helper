from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import login_required

from app.services.image_generation_service import ImageGenerationError, generate_ad_image
from app.services.product_extension_service import ASPECT_RATIOS, generate_product_concepts


bp = Blueprint("product_extension", __name__, url_prefix="/product-extension")


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    form = {
        "product_name": request.form.get("product_name", "旅行保温杯"),
        "audience": request.form.get("audience", "通勤人群"),
        "selling_point": request.form.get("selling_point", "长效保温、防漏、轻便"),
        "ad_copy": request.form.get("ad_copy", "Everyday upgrade"),
        "aspect_ratio": request.form.get("aspect_ratio", "1:1"),
        "image_provider": request.form.get("image_provider", current_app.config.get("IMAGE_PROVIDER", "openai")),
    }
    concepts, source = generate_product_concepts(
        form,
        api_key=current_app.config.get("GEMINI_API_KEY") if request.method == "POST" else None,
        model=current_app.config.get("GEMINI_MODEL"),
    )
    return render_template(
        "product_extension/index.html",
        page_title="产品扩展",
        form=form,
        aspect_ratios=ASPECT_RATIOS,
        concepts=concepts,
        source=source,
    )


@bp.post("/generate-image")
@login_required
def generate_image():
    payload = request.get_json(silent=True) or {}
    prompt = (payload.get("prompt") or "").strip()
    aspect_ratio = payload.get("aspect_ratio") or "1:1"
    provider = payload.get("provider") or current_app.config.get("IMAGE_PROVIDER", "openai")
    if not prompt:
        return jsonify({"error": "Missing image prompt."}), 400
    try:
        return jsonify(generate_ad_image(prompt, aspect_ratio, provider=provider))
    except ImageGenerationError as exc:
        return jsonify({"error": str(exc)}), 502
