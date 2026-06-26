from pathlib import Path

from flask import Flask, redirect, render_template, url_for
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

from app.config import ProductionConfig
from app.extensions import db, login_manager, scheduler
from app.models import CollectionTask, CompetitorProduct, CompetitorTask, User
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(competitor_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(hashtag_discovery_bp)
    app.register_blueprint(product_extension_bp)
    app.register_blueprint(xiaohongshu_bp)

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
    if "product_created_at" not in product_columns:
        db.session.execute(text("ALTER TABLE competitor_products ADD COLUMN product_created_at DATETIME"))
        db.session.commit()
    if "product_tags" not in product_columns:
        db.session.execute(text("ALTER TABLE competitor_products ADD COLUMN product_tags TEXT"))
        db.session.commit()
