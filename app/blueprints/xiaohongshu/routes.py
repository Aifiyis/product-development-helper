import json

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import CollectionTask, CollectedNote
from app.permissions import permission_required
from app.services.export_service import build_notes_csv
from app.services.scheduler_service import register_task_job


bp = Blueprint("xiaohongshu", __name__, url_prefix="/collection/platform")
PLATFORM = "platform"
PLATFORM_LABEL = "平台采集"
PLATFORM_CHOICES = [
    ("xiaohongshu", "小红书"),
    ("douyin", "抖音"),
    ("tiktok", "tiktok"),
    ("instagram", "Instagram"),
    ("facebook", "facebook"),
    ("pinterest", "pinterest"),
]


@bp.get("")
@login_required
@permission_required("platform_collection.view")
def index():
    notes = CollectedNote.query.order_by(CollectedNote.collection_time.desc()).all()
    tasks = (
        CollectionTask.query.filter_by(platform=PLATFORM)
        .order_by(CollectionTask.created_at.desc())
        .all()
    )
    return render_template(
        "collection/index.html",
        page_title=PLATFORM_LABEL,
        endpoint="xiaohongshu",
        platform_label=PLATFORM_LABEL,
        platform_choices=PLATFORM_CHOICES,
        notes=notes,
        tasks=tasks,
    )


@bp.post("/tasks")
@login_required
@permission_required("platform_collection.create_task")
def create_task():
    selected_platforms = [
        value for value in request.form.getlist("collection_platforms")
        if value in {choice[0] for choice in PLATFORM_CHOICES}
    ]
    if not selected_platforms:
        selected_platforms = ["xiaohongshu"]

    task = CollectionTask(
        platform=PLATFORM,
        collection_platforms=",".join(selected_platforms),
        product_keywords=request.form.get("product_keywords", "").strip(),
        comment_monitor_keywords=request.form.get("comment_monitor_keywords", "").strip(),
        min_likes=int(request.form.get("min_likes") or 0),
        collection_cycle=request.form.get("collection_cycle", "6h"),
        status="active",
        created_by=current_user.id,
    )
    db.session.add(task)
    db.session.commit()
    register_task_job(task, current_app._get_current_object())
    return redirect(url_for("xiaohongshu.index"))


@bp.get("/notes/<int:note_id>")
@login_required
@permission_required("platform_collection.detail")
def note_detail(note_id):
    note = CollectedNote.query.get_or_404(note_id)
    return jsonify(
        {
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "author": note.author,
            "product_keyword": note.product_keyword,
            "likes_count": note.likes_count,
            "comments_count": note.comments_count,
            "publish_time": note.publish_time.strftime("%Y-%m-%d %H:%M") if note.publish_time else "",
            "collection_time": note.collection_time.strftime("%Y-%m-%d %H:%M") if note.collection_time else "",
            "source_url": note.source_url,
            "triggered_comments": json.loads(note.triggered_comments or "[]"),
            "extra_data": json.loads(note.extra_data or "{}"),
        }
    )


@bp.post("/export")
@login_required
@permission_required("platform_collection.export")
def export():
    csv_file = build_notes_csv(PLATFORM)
    return send_file(
        csv_file,
        as_attachment=True,
        download_name="platform_notes.csv",
        mimetype="text/csv",
    )
