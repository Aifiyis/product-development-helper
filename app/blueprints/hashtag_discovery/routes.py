import csv
import json
from io import BytesIO, StringIO

from flask import Blueprint, current_app, render_template, request, send_file
from flask_login import login_required

from app.services.hashtag_discovery_service import (
    AD_CATEGORIES,
    LANGUAGES,
    PLATFORMS,
    discover_trends,
)


bp = Blueprint("hashtag_discovery", __name__, url_prefix="/hashtag-discovery")


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    filters = {
        "platform": request.form.get("platform", "TikTok"),
        "language": request.form.get("language", "英语"),
        "category": request.form.get("category", "通用"),
    }
    results = discover_trends(
        platform=filters["platform"],
        language=filters["language"],
        category=filters["category"],
        api_key=current_app.config.get("GEMINI_API_KEY") if request.method == "POST" else None,
        model=current_app.config.get("GEMINI_MODEL"),
    )
    return render_template(
        "hashtag_discovery/index.html",
        page_title="热门标签发现",
        platforms=PLATFORMS,
        languages=LANGUAGES,
        categories=AD_CATEGORIES,
        filters=filters,
        results=results,
    )


@bp.post("/export")
@login_required
def export():
    try:
        payload = json.loads(request.form.get("payload") or "{}")
    except json.JSONDecodeError:
        payload = {}
    csv_file = build_trends_csv(payload)
    filename = f"hashtag_trends_{payload.get('platform', 'platform')}_{payload.get('language', 'language')}.csv"
    return send_file(csv_file, as_attachment=True, download_name=filename, mimetype="text/csv")


def build_trends_csv(payload):
    text_buffer = StringIO()
    text_buffer.write("\ufeff")
    writer = csv.writer(text_buffer)
    writer.writerow(["类型", "平台", "语种", "广告类目", "名称", "热度", "趋势", "说明"])

    common = [payload.get("platform", ""), payload.get("language", ""), payload.get("category", "")]
    for item in payload.get("hashtags", []):
        writer.writerow(["Hashtag", *common, item.get("tag", ""), item.get("volume", ""), item.get("trend", ""), item.get("insight", "")])
    for item in payload.get("topics", []):
        writer.writerow(["Topic", *common, item.get("title", ""), item.get("volume", ""), item.get("trend", ""), item.get("insight", "")])

    return BytesIO(text_buffer.getvalue().encode("utf-8-sig"))
