from functools import wraps

from flask import abort, flash, redirect, url_for
from flask_login import current_user


ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_EMPLOYEE = "employee"

ROLE_LABELS = {
    ROLE_SUPER_ADMIN: "超级管理员",
    ROLE_ADMIN: "管理员",
    ROLE_EMPLOYEE: "员工",
}

LEGACY_ROLE_MAP = {
    "viewer": ROLE_EMPLOYEE,
}

PERMISSION_GROUPS = [
    {
        "title": "页面权限",
        "items": [
            ("dashboard.view", "中控台"),
            ("users.manage", "用户管理"),
            ("hashtag.view", "热门标签发现"),
            ("product_extension.view", "产品扩展"),
            ("trends.view", "产品趋势库"),
            ("competitor.view", "产品抓取"),
            ("product_inbox.view", "产品认领箱"),
            ("stores.view", "店铺管理"),
            ("platform_collection.view", "平台采集"),
            ("reports.view", "分析报告"),
        ],
    },
    {
        "title": "热门标签发现",
        "items": [
            ("hashtag.catch", "Catch 生成趋势"),
            ("hashtag.export", "导出 CSV"),
        ],
    },
    {
        "title": "产品抓取",
        "items": [
            ("competitor.create_task", "创建采集任务"),
            ("competitor.run_task", "运行采集任务"),
            ("competitor.pause_task", "暂停任务"),
            ("competitor.delete_task", "删除任务"),
            ("competitor.export", "导出 CSV"),
            ("competitor.detail", "查看产品详情"),
            ("competitor.manage_sites", "管理竞品网站"),
            ("competitor.discover_sites", "AI 发现竞品网站"),
        ],
    },
    {
        "title": "产品认领与发布",
        "items": [
            ("product_inbox.move", "移入产品认领箱"),
            ("product_inbox.claim", "认领产品"),
            ("product_inbox.edit", "编辑店铺商品"),
            ("product_inbox.create_draft", "创建店铺草稿"),
            ("product_inbox.publish", "发布商品"),
            ("stores.manage", "管理店铺连接"),
        ],
    },
    {
        "title": "平台采集",
        "items": [
            ("platform_collection.create_task", "创建采集任务"),
            ("platform_collection.detail", "查看采集详情"),
            ("platform_collection.export", "导出 CSV"),
        ],
    },
    {
        "title": "产品扩展",
        "items": [
            ("product_extension.generate", "生成广告概念"),
            ("product_extension.generate_image", "生成广告图片"),
        ],
    },
]

ALL_PERMISSIONS = [permission for group in PERMISSION_GROUPS for permission, _ in group["items"]]

DEFAULT_ROLE_PERMISSIONS = {
    ROLE_ADMIN: [
        "dashboard.view",
        "users.manage",
        "hashtag.view",
        "hashtag.catch",
        "hashtag.export",
        "product_extension.view",
        "product_extension.generate",
        "product_extension.generate_image",
        "trends.view",
        "competitor.view",
        "competitor.create_task",
        "competitor.run_task",
        "competitor.pause_task",
        "competitor.delete_task",
        "competitor.export",
        "competitor.detail",
        "competitor.manage_sites",
        "competitor.discover_sites",
        "platform_collection.view",
        "product_inbox.view",
        "product_inbox.move",
        "product_inbox.claim",
        "product_inbox.edit",
        "product_inbox.create_draft",
        "product_inbox.publish",
        "stores.view",
        "stores.manage",
        "platform_collection.create_task",
        "platform_collection.detail",
        "platform_collection.export",
        "reports.view",
    ],
    ROLE_EMPLOYEE: [
        "dashboard.view",
        "hashtag.view",
        "hashtag.catch",
        "hashtag.export",
        "competitor.view",
        "competitor.detail",
        "competitor.export",
        "product_inbox.view",
        "stores.view",
    ],
}

PAGE_PERMISSIONS = {
    "dashboard": "dashboard.view",
    "users": "users.manage",
    "hashtag": "hashtag.view",
    "product_extension": "product_extension.view",
    "trends": "trends.view",
    "competitor": "competitor.view",
    "platform_collection": "platform_collection.view",
    "product_workflow": "product_inbox.view",
    "reports": "reports.view",
}

LANDING_ENDPOINTS = [
    ("dashboard.view", "dashboard.index"),
    ("hashtag.view", "hashtag_discovery.index"),
    ("competitor.view", "competitor.index"),
    ("product_extension.view", "product_extension.index"),
    ("platform_collection.view", "xiaohongshu.index"),
    ("trends.view", "dashboard.trends"),
    ("reports.view", "dashboard.reports"),
    ("users.manage", "auth.users"),
]


def normalize_role(role):
    return LEGACY_ROLE_MAP.get(role, role or ROLE_EMPLOYEE)


def landing_url_for(user):
    for permission, endpoint in LANDING_ENDPOINTS:
        if user.can(permission):
            return url_for(endpoint)
    return url_for("auth.logout")


def permission_required(permission):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.can(permission):
                if permission == "dashboard.view":
                    abort(403)
                flash("当前账号没有访问该功能的权限。", "warning")
                return redirect(landing_url_for(current_user))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator

