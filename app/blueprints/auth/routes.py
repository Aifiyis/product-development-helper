from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import User


bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(request.args.get("next") or url_for("dashboard.index"))
        flash("用户名或密码错误。", "danger")

    return render_template("auth/login.html", page_title="登录")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/users", methods=["GET", "POST"])
@login_required
def users():
    if not current_user.is_admin():
        flash("只有管理员可以管理用户。", "warning")
        return redirect(url_for("dashboard.index"))

    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "viewer")
            if not username or not password:
                flash("用户名和密码不能为空。", "danger")
            elif User.query.filter_by(username=username).first():
                flash("用户名已存在。", "danger")
            else:
                db.session.add(
                    User(
                        username=username,
                        password_hash=generate_password_hash(password),
                        role=role if role in {"admin", "viewer"} else "viewer",
                        is_active=True,
                    )
                )
                db.session.commit()
                flash("用户已创建。", "success")

        if action == "update":
            user = User.query.get_or_404(int(request.form.get("user_id")))
            user.role = request.form.get("role", user.role)
            user.is_active = request.form.get("is_active") == "1"
            password = request.form.get("password", "")
            if password:
                user.password_hash = generate_password_hash(password)
            db.session.commit()
            flash("用户已更新。", "success")

        if action == "delete":
            user = User.query.get_or_404(int(request.form.get("user_id")))
            if user.id == current_user.id:
                flash("不能删除当前登录用户。", "danger")
            else:
                db.session.delete(user)
                db.session.commit()
                flash("用户已删除。", "success")

        return redirect(url_for("auth.users"))

    users_list = User.query.order_by(User.created_at.desc()).all()
    return render_template("auth/users.html", page_title="用户管理", users=users_list)
