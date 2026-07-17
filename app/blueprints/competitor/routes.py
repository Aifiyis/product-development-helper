import json
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db, scheduler
from app.models import CompetitorProduct, CompetitorTask
from app.models import ProductInboxItem
from app.permissions import permission_required
from app.services.competitor_export_service import build_products_csv
from app.services.competitor_service import add_competitor, add_discovered, load_competitors, list_by_type
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

PRODUCT_SORT_COLUMNS = {
    "product_created_at": CompetitorProduct.product_created_at,
    "reviews_count": CompetitorProduct.reviews_count,
    "fb_ad_count": CompetitorProduct.fb_ad_count,
}


def resolve_product_sort(sort_field, sort_direction):
    selected_field = sort_field if sort_field in PRODUCT_SORT_COLUMNS else None
    selected_direction = sort_direction if sort_direction in {"asc", "desc"} else "desc"
    if selected_field is None:
        return (
            CompetitorProduct.collected_at.desc(),
            CompetitorProduct.id.desc(),
        ), None, "desc"

    column = PRODUCT_SORT_COLUMNS[selected_field]
    primary_order = column.asc().nullslast() if selected_direction == "asc" else column.desc().nullslast()
    return (
        primary_order,
        CompetitorProduct.collected_at.desc(),
        CompetitorProduct.id.desc(),
    ), selected_field, selected_direction


@bp.get("")
@login_required
@permission_required("competitor.view")
def index():
    data = load_competitors()
    tasks = CompetitorTask.query.order_by(CompetitorTask.created_at.desc()).all()
    product_ordering, product_sort, product_sort_direction = resolve_product_sort(
        request.args.get("sort"),
        request.args.get("direction"),
    )
    products = CompetitorProduct.query.order_by(*product_ordering).limit(200).all()
    inbox_product_ids = {
        source_id for (source_id,) in db.session.query(ProductInboxItem.source_product_id).filter(
            ProductInboxItem.source_product_id.isnot(None)
        ).all()
    }
    return render_template(
        "competitor/index.html",
        page_title="产品抓取",
        categories=data["categories"],
        competitors=data["competitors"],
        platform_labels=PLATFORM_LABELS,
        tasks=tasks,
        inbox_product_ids=inbox_product_ids,
        products=products,
        product_sort=product_sort,
        product_sort_direction=product_sort_direction,
    )


@bp.post("/tasks")
@login_required
@permission_required("competitor.create_task")
def create_task():
    collection_mode = request.form.get("collection_mode", "competitor_sites")
    if collection_mode not in {"competitor_sites", "product_links"}:
        collection_mode = "competitor_sites"

    sites = request.form.getlist("target_sites") if collection_mode == "competitor_sites" else []
    if not sites and collection_mode == "competitor_sites":
        sites = [request.form.get("target_sites", "")]
    product_urls = parse_product_urls(request.form.get("product_urls", "")) if collection_mode == "product_links" else []
    if collection_mode == "competitor_sites" and not any(site.strip() for site in sites):
        return task_request_error("请至少选择一个目标网站。")
    if collection_mode == "product_links" and not product_urls:
        return task_request_error("请填写至少一个有效的产品网址（以 http:// 或 https:// 开头）。")

    task = CompetitorTask(
        target_sites=",".join(site for site in sites if site),
        collection_mode=collection_mode,
        product_urls="\n".join(product_urls),
        target_category=request.form.get("target_category", "") if collection_mode == "competitor_sites" else "",
        product_keywords=request.form.get("product_keywords", "").strip() if collection_mode == "competitor_sites" else "",
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
@permission_required("competitor.run_task")
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
@permission_required("competitor.view")
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
    is_link_collection = task.is_product_link_collection
    return {
        "id": task.id,
        "collection_mode": task.collection_mode,
        "category_label": "链接采集" if is_link_collection else (categories.get(task.target_category, task.target_category) if task.target_category else "不限"),
        "sites": task.product_url_list if is_link_collection else task.site_list,
        "product_keywords": "-" if is_link_collection else (task.product_keywords or "-"),
        "condition": f"逐链接采集 · {len(task.product_url_list)} 条" if is_link_collection else f'{task.products_per_site} 条/站 · {"最新上架" if task.sort_mode == "newest" else "销量排名"}',
        "cycle_label": "即时" if task.collection_cycle == "instant" else task.collection_cycle,
        "collection_cycle": task.collection_cycle,
        "status": task.status,
        "status_label": task.status_label,
        "status_badge": task.status_badge,
    }


@bp.post("/tasks/<int:task_id>/pause")
@login_required
@permission_required("competitor.pause_task")
def pause_task(task_id):
    task = CompetitorTask.query.get_or_404(task_id)
    if task.scheduler_job_id and scheduler.get_job(task.scheduler_job_id):
        scheduler.remove_job(task.scheduler_job_id)
    task.status = "paused"
    db.session.commit()
    return redirect(url_for("competitor.index"))


@bp.post("/tasks/<int:task_id>/delete")
@login_required
@permission_required("competitor.delete_task")
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
@permission_required("competitor.detail")
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
@permission_required("competitor.export")
def export():
    task_id = request.form.get("task_id") or None
    csv_file = build_products_csv(task_id=task_id)
    return send_file(csv_file, as_attachment=True, download_name="competitor_products.csv", mimetype="text/csv")


@bp.get("/sites")
@login_required
@permission_required("competitor.manage_sites")
def sites():
    categories, grouped = list_by_type()
    return render_template(
        "competitor/sites.html",
        page_title="竞品站列表",
        categories=categories,
        grouped=grouped,
        platform_labels=PLATFORM_LABELS,
    )


@bp.post("/sites/add")
@login_required
@permission_required("competitor.manage_sites")
def add_site():
    data = load_competitors()
    category = request.form.get("category", "comprehensive")
    platform = request.form.get("platform", "unknown")
    if category not in data.get("categories", {}):
        category = "comprehensive"
    if platform not in PLATFORM_LABELS:
        platform = "unknown"

    created = add_competitor(
        request.form.get("domain", ""),
        category,
        request.form.get("description", "").strip(),
        request.form.get("scrape_reason", "").strip(),
        platform=platform,
        source="manual",
    )
    flash("站点已添加。" if created else "站点未添加：网址为空或已存在。", "success" if created else "warning")
    return redirect(url_for("competitor.sites"))


@bp.post("/sites/track")
@login_required
@permission_required("competitor.discover_sites")
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


def parse_product_urls(raw_urls):
    urls = []
    seen = set()
    for line in (raw_urls or "").splitlines():
        url = line.strip()
        parsed = urlparse(url)
        if not url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def task_request_error(message):
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"message": message}), 400
    flash(message, "danger")
    return redirect(url_for("competitor.index"))
