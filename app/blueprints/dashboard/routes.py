from flask import Blueprint, render_template, url_for
from flask_login import current_user, login_required

from app.models import CollectionTask, CollectedNote, CompetitorProduct, CompetitorTask, User
from app.permissions import permission_required


bp = Blueprint("dashboard", __name__)


@bp.get("/dashboard")
@login_required
@permission_required("dashboard.view")
def index():
    modules = [
        {
            "title": "产品扩展",
            "description": "延伸产品使用场景与广告卖点",
            "tag": "创意拓展",
            "icon": "bi-card-image",
            "url": url_for("product_extension.index"),
            "permission": "product_extension.view",
        },
        {
            "title": "热门标签发现",
            "description": "发现多平台多语种上升趋势标签",
            "tag": "趋势洞察",
            "icon": "bi-card-image",
            "url": url_for("hashtag_discovery.index"),
            "permission": "hashtag.view",
        },
        {
            "title": "产品趋势库",
            "description": "聚合高热内容与关键词变化",
            "tag": "数据分析",
            "icon": "bi-card-image",
            "url": url_for("dashboard.trends"),
            "permission": "trends.view",
        },
        {
            "title": "产品抓取",
            "description": "跟踪竞品内容声量与互动变化",
            "tag": "监控",
            "icon": "bi-card-image",
            "url": url_for("competitor.index"),
            "permission": "competitor.view",
        },
        {
            "title": "平台采集",
            "description": "按平台监控内容与评论触发词",
            "tag": "内容采集",
            "icon": "bi-card-image",
            "url": url_for("xiaohongshu.index"),
            "permission": "platform_collection.view",
        },
        {
            "title": "分析报告",
            "description": "为单条内容沉淀产品分析入口",
            "tag": "报告",
            "icon": "bi-card-image",
            "url": url_for("dashboard.reports"),
            "permission": "reports.view",
        },
    ]
    modules = [module for module in modules if current_user.can(module["permission"])]
    stats = [
        {"label": "功能模块", "value": len(modules), "icon": "bi-grid-3x3-gap-fill", "tone": "primary"},
        {"label": "总任务数", "value": CollectionTask.query.count() + CompetitorTask.query.count(), "icon": "bi-clipboard2-check-fill", "tone": "success"},
        {"label": "已完成", "value": CollectedNote.query.count() + CompetitorProduct.query.count(), "icon": "bi-check-circle-fill", "tone": "warning"},
        {"label": "用户数", "value": User.query.count(), "icon": "bi-person-fill", "tone": "secondary"},
    ]
    return render_template("dashboard/index.html", page_title="中控台", stats=stats, modules=modules)


@bp.get("/trends")
@login_required
@permission_required("trends.view")
def trends():
    return render_template("dashboard/placeholder.html", page_title="产品趋势库", title="产品趋势库")


@bp.get("/reports")
@login_required
@permission_required("reports.view")
def reports():
    return render_template("dashboard/placeholder.html", page_title="分析报告", title="分析报告")

