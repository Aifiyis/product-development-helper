from pathlib import Path

from flask import Flask, redirect, render_template, url_for
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

from app.config import ProductionConfig
from app.extensions import db, login_manager, scheduler
from app.models import CollectionTask, CompetitorProduct, CompetitorTask, User
from app.models import ProductInboxItem, StoreConnection, StoreProductDraft
from app.permissions import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.services.scheduler_service import restore_active_jobs


def create_app(config_object=ProductionConfig):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "请先登录后再访问。"

    register_blueprints(app)
    register_error_handlers(app)
    register_shell_context(app)

    with app.app_context():
        db.create_all()
        ensure_schema_columns()
        ensure_default_admin()

    scheduler.init_app(app)
    if not scheduler.running:
        scheduler.start()
    with app.app_context():
        restore_active_jobs(app)

    return app


def register_blueprints(app):
    from app.blueprints.auth.routes import bp as auth_bp
    from app.blueprints.competitor.routes import bp as competitor_bp
    from app.blueprints.dashboard.routes import bp as dashboard_bp
    from app.blueprints.hashtag_discovery.routes import bp as hashtag_discovery_bp
    from app.blueprints.product_extension.routes import bp as product_extension_bp
    from app.blueprints.xiaohongshu.routes import bp as xiaohongshu_bp
    from app.blueprints.product_workflow.routes import bp as product_workflow_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(competitor_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(hashtag_discovery_bp)
    app.register_blueprint(product_extension_bp)
    app.register_blueprint(xiaohongshu_bp)
    app.register_blueprint(product_workflow_bp)

    @app.get("/")
    def index():
        return redirect(url_for("dashboard.index"))


def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(error):
        return render_template("base.html", page_title="页面不存在", error=error), 404

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("base.html", page_title="没有权限", error=error), 403


def register_shell_context(app):
    @app.shell_context_processor
    def context():
        return {
            "db": db,
            "User": User,
            "CollectionTask": CollectionTask,
            "CompetitorTask": CompetitorTask,
            "CompetitorProduct": CompetitorProduct,
            "ProductInboxItem": ProductInboxItem,
            "StoreConnection": StoreConnection,
            "StoreProductDraft": StoreProductDraft,
        }


def ensure_default_admin():
    if User.query.filter_by(role=ROLE_SUPER_ADMIN).first():
        return
    existing = User.query.filter_by(username="admin").first()
    if existing:
        if existing.role == ROLE_ADMIN:
            existing.role = ROLE_SUPER_ADMIN
            db.session.commit()
        return
    admin = User(
        username="admin",
        password_hash=generate_password_hash("admin123"),
        role=ROLE_SUPER_ADMIN,
        is_active=True,
    )
    db.session.add(admin)
    db.session.commit()


def ensure_schema_columns():
    user_columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
    if "permissions" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN permissions TEXT"))
        db.session.commit()
    if "parent_id" not in user_columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN parent_id INTEGER"))
        db.session.commit()
    columns = {column["name"] for column in inspect(db.engine).get_columns("collection_tasks")}
    if "collection_platforms" not in columns:
        db.session.execute(text("ALTER TABLE collection_tasks ADD COLUMN collection_platforms TEXT"))
        db.session.commit()
    competitor_columns = {column["name"] for column in inspect(db.engine).get_columns("competitor_tasks")}
    if "collection_mode" not in competitor_columns:
        db.session.execute(text("ALTER TABLE competitor_tasks ADD COLUMN collection_mode TEXT"))
        db.session.commit()
    if "product_urls" not in competitor_columns:
        db.session.execute(text("ALTER TABLE competitor_tasks ADD COLUMN product_urls TEXT"))
        db.session.commit()
    if "category_url" not in competitor_columns:
        db.session.execute(text("ALTER TABLE competitor_tasks ADD COLUMN category_url VARCHAR(1000)"))
        db.session.commit()
    if "category_scope" not in competitor_columns:
        db.session.execute(text("ALTER TABLE competitor_tasks ADD COLUMN category_scope VARCHAR(20) DEFAULT 'pages'"))
        db.session.commit()
    if "category_page_count" not in competitor_columns:
        db.session.execute(text("ALTER TABLE competitor_tasks ADD COLUMN category_page_count INTEGER DEFAULT 1"))
        db.session.commit()
    if "product_keywords" not in competitor_columns:
        db.session.execute(text("ALTER TABLE competitor_tasks ADD COLUMN product_keywords TEXT"))
        db.session.commit()
    if "sort_mode" not in competitor_columns:
        db.session.execute(text("ALTER TABLE competitor_tasks ADD COLUMN sort_mode TEXT"))
        db.session.commit()
    if "last_error" not in competitor_columns:
        db.session.execute(text("ALTER TABLE competitor_tasks ADD COLUMN last_error TEXT"))
        db.session.commit()
    if "last_run_summary" not in competitor_columns:
        db.session.execute(text("ALTER TABLE competitor_tasks ADD COLUMN last_run_summary TEXT"))
        db.session.commit()
    product_columns = {column["name"] for column in inspect(db.engine).get_columns("competitor_products")}
    if "platform" not in product_columns:
        db.session.execute(text("ALTER TABLE competitor_products ADD COLUMN platform VARCHAR(30)"))
        db.session.execute(text("UPDATE competitor_products SET platform = 'unknown' WHERE platform IS NULL OR platform = ''"))
        db.session.commit()
    if "product_created_at" not in product_columns:
        db.session.execute(text("ALTER TABLE competitor_products ADD COLUMN product_created_at DATETIME"))
        db.session.commit()
    if "product_tags" not in product_columns:
        db.session.execute(text("ALTER TABLE competitor_products ADD COLUMN product_tags TEXT"))
        db.session.commit()
    if "previous_price" not in product_columns:
        db.session.execute(text("ALTER TABLE competitor_products ADD COLUMN previous_price VARCHAR(80)"))
        db.session.commit()
    if "previous_collected_at" not in product_columns:
        db.session.execute(text("ALTER TABLE competitor_products ADD COLUMN previous_collected_at DATETIME"))
        db.session.commit()
    inbox_columns = {column["name"] for column in inspect(db.engine).get_columns("product_inbox_items")}
    if "options_json" not in inbox_columns:
        db.session.execute(text("ALTER TABLE product_inbox_items ADD COLUMN options_json TEXT"))
        db.session.commit()
    if "base_sku" not in inbox_columns:
        db.session.execute(text("ALTER TABLE product_inbox_items ADD COLUMN base_sku VARCHAR(255)"))
        db.session.commit()
    inbox_image_columns = {column["name"] for column in inspect(db.engine).get_columns("inbox_product_images")}
    if "local_path" not in inbox_image_columns:
        db.session.execute(text("ALTER TABLE inbox_product_images ADD COLUMN local_path VARCHAR(1000)"))
        db.session.commit()
    inbox_variant_columns = {column["name"] for column in inspect(db.engine).get_columns("inbox_variants")}
    if "local_image_path" not in inbox_variant_columns:
        db.session.execute(text("ALTER TABLE inbox_variants ADD COLUMN local_image_path VARCHAR(1000)"))
        db.session.commit()
    draft_columns = {column["name"] for column in inspect(db.engine).get_columns("store_product_drafts")}
    if "product_metafields_json" not in draft_columns:
        db.session.execute(text("ALTER TABLE store_product_drafts ADD COLUMN product_metafields_json TEXT"))
        db.session.commit()
    if "base_sku" not in draft_columns:
        db.session.execute(text("ALTER TABLE store_product_drafts ADD COLUMN base_sku VARCHAR(255)"))
        db.session.commit()
    image_columns = {column["name"] for column in inspect(db.engine).get_columns("draft_product_images")}
    if "remote_media_id" not in image_columns:
        db.session.execute(text("ALTER TABLE draft_product_images ADD COLUMN remote_media_id VARCHAR(255)"))
        db.session.commit()
    variant_columns = {column["name"] for column in inspect(db.engine).get_columns("draft_variants")}
    if "remote_media_id" not in variant_columns:
        db.session.execute(text("ALTER TABLE draft_variants ADD COLUMN remote_media_id VARCHAR(255)"))
        db.session.commit()
