import json

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import CompetitorProduct, CompetitorTask
from app.services.competitor_export_service import build_products_csv
from app.services.competitor_service import add_discovered, load_competitors, list_by_type
from app.services.scheduler_service import register_competitor_job, run_competitor_task_by_id
from app.services.trend_tracking_service import discover_competitors


bp = Blueprint("competitor", __name__, url_prefix="/competitor")


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
        target_category=request.form.get("target_category", "comprehensive"),
        products_per_site=int(request.form.get("products_per_site") or 20),
        fb_ad_threshold=int(request.form.get("fb_ad_threshold") or 0),
        collection_cycle=request.form.get("collection_cycle", "6h"),
        status="active",
        created_by=current_user.id,
    )
    db.session.add(task)
    db.session.commit()
    register_competitor_job(task, current_app._get_current_object())
    return redirect(url_for("competitor.index"))


@bp.post("/tasks/<int:task_id>/run")
@login_required
def run_task(task_id):
    run_competitor_task_by_id(task_id, current_app._get_current_object())
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
        )
    return redirect(url_for("competitor.sites"))
