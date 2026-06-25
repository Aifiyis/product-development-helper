import json

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db, scheduler
from app.models import CompetitorProduct, CompetitorTask
from app.services.competitor_export_service import build_products_csv
from app.services.competitor_service import add_discovered, load_competitors, list_by_type
from app.services.scheduler_service import enqueue_competitor_task, register_competitor_job
from app.services.trend_tracking_service import discover_competitors


bp = Blueprint("competitor", __name__, url_prefix="/competitor")

PLATFORM_LABELS = {
    "shopify": "Shopify",
    "shopline": "Shopline",
    "shoplazza": "Shoplazza",
    "custom": "自建站",
    "unknown": "未知",
}


@bp.get("")
@login_required
def index():
    data = load_competitors()
    tasks = CompetitorTask.query.order_by(CompetitorTask.created_at.desc()).all()
    products = CompetitorProduct.query.order_by(CompetitorProduct.collected_at.desc()).limit(200).all()
    return render_template(
        "competitor/index.html",
        page_title="竞品监控",
        categories=data["categories"],
        competitors=data["competitors"],
        platform_labels=PLATFORM_LABELS,
        tasks=tasks,
        products=products,
    )


@bp.post("/tasks")
@login_required
def create_task():
    sites = request.form.getlist("target_sites")
    if not sites:
        sites = [request.form.get("target_sites", "")]
    task = CompetitorTask(
        target_sites=",".join(site for site in sites if site),
        target_category=request.form.get("target_category", ""),
        product_keywords=request.form.get("product_keywords", "").strip(),
        sort_mode=request.form.get("sort_mode", "best_selling"),
        products_per_site=int(request.form.get("products_per_site") or 20),
        fb_ad_threshold=0,
        collection_cycle=request.form.get("collection_cycle", "instant"),
        status="collecting",
        created_by=current_user.id,
    )
    db.session.add(task)
    db.session.commit()
    if task.collection_cycle == "instant":
        enqueue_competitor_task(task.id, current_app._get_current_object(), complete_instant=True)
    else:
        register_competitor_job(task, current_app._get_current_object())
        enqueue_competitor_task(task.id, current_app._get_current_object())
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        db.session.refresh(task)
        return jsonify({"task": serialize_task(task, load_competitors()["categories"]), "message": "后台采集中"})
    return redirect(url_for("competitor.index"))


@bp.post("/tasks/<int:task_id>/run")
@login_required
def run_task(task_id):
    task = CompetitorTask.query.get_or_404(task_id)
    if task.status in {"failed", "paused", "completed"}:
        task.status = "collecting"
        db.session.commit()
    enqueue_competitor_task(task_id, current_app._get_current_object(), complete_instant=task.collection_cycle == "instant")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"task": serialize_task(task, load_competitors()["categories"]), "message": "后台采集中"})
    return redirect(url_for("competitor.index"))


@bp.get("/tasks/<int:task_id>/status")
@login_required
def task_status(task_id):
    task = CompetitorTask.query.get_or_404(task_id)
    return jsonify(
        {
            "task_id": task.id,
            "status": task.status,
            "status_label": task.status_label,
            "status_badge": task.status_badge,
            "last_error": task.last_error or "",
            "last_run_at": task.last_run_at.strftime("%Y-%m-%d %H:%M") if task.last_run_at else "",
        }
    )


def serialize_task(task, categories):
    return {
        "id": task.id,
        "category_label": categories.get(task.target_category, task.target_category) if task.target_category else "不限",
        "sites": task.site_list,
        "product_keywords": task.product_keywords or "-",
        "condition": f'{task.products_per_site} 条/站 · {"最新上架" if task.sort_mode == "newest" else "销量排名"}',
        "cycle_label": "即时" if task.collection_cycle == "instant" else task.collection_cycle,
        "collection_cycle": task.collection_cycle,
        "status": task.status,
        "status_label": task.status_label,
        "status_badge": task.status_badge,
    }


@bp.post("/tasks/<int:task_id>/pause")
@login_required
def pause_task(task_id):
    task = CompetitorTask.query.get_or_404(task_id)
    if task.scheduler_job_id and scheduler.get_job(task.scheduler_job_id):
        scheduler.remove_job(task.scheduler_job_id)
    task.status = "paused"
    db.session.commit()
    return redirect(url_for("competitor.index"))


@bp.post("/tasks/<int:task_id>/delete")
@login_required
def delete_task(task_id):
    task = CompetitorTask.query.get_or_404(task_id)
    if task.scheduler_job_id and scheduler.get_job(task.scheduler_job_id):
        scheduler.remove_job(task.scheduler_job_id)
    delete_mode = request.form.get("delete_mode", "task_only")
    if delete_mode == "task_with_data":
        CompetitorProduct.query.filter_by(task_id=task.id).delete(synchronize_session=False)
    else:
        CompetitorProduct.query.filter_by(task_id=task.id).update({"task_id": None}, synchronize_session=False)
    db.session.delete(task)
    db.session.commit()
    return redirect(url_for("competitor.index"))


@bp.get("/products/<int:product_id>")
@login_required
def product_detail(product_id):
    product = CompetitorProduct.query.get_or_404(product_id)
    return jsonify(
        {
            "id": product.id,
            "source_domain": product.source_domain,
            "source_type": product.source_type,
            "title": product.title,
            "price": product.price,
            "product_created_at": product.product_created_at.strftime("%Y-%m-%d %H:%M") if product.product_created_at else "",
            "product_tags": json.loads(product.product_tags or "[]"),
            "product_media": json.loads(product.product_media or "{}"),
            "reviews_count": product.reviews_count,
            "variants": json.loads(product.variants or "[]"),
            "description": product.description,
            "product_url": product.product_url,
            "fb_ad_count": product.fb_ad_count,
            "matched_ad": json.loads(product.matched_ad or "{}"),
            "collected_at": product.collected_at.strftime("%Y-%m-%d %H:%M") if product.collected_at else "",
        }
    )


@bp.post("/export")
@login_required
def export():
    task_id = request.form.get("task_id") or None
    csv_file = build_products_csv(task_id=task_id)
    return send_file(csv_file, as_attachment=True, download_name="competitor_products.csv", mimetype="text/csv")


@bp.get("/sites")
@login_required
def sites():
    categories, grouped = list_by_type()
    return render_template(
        "competitor/sites.html",
        page_title="竞品站列表",
        categories=categories,
        grouped=grouped,
        platform_labels=PLATFORM_LABELS,
    )


@bp.post("/sites/track")
@login_required
def track_sites():
    keyword = request.form.get("keyword", "").strip()
    category = request.form.get("category", "comprehensive")
    candidates = discover_competitors(keyword, category, current_app.config)
    for candidate in candidates:
        add_discovered(
            candidate.get("domain"),
            category,
            candidate.get("description"),
            candidate.get("scrape_reason"),
            candidate.get("platform", "unknown"),
        )
    return redirect(url_for("competitor.sites"))
