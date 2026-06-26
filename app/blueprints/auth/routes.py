from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models import User
from app.permissions import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_GROUPS,
    ROLE_ADMIN,
    ROLE_EMPLOYEE,
    ROLE_LABELS,
    ROLE_SUPER_ADMIN,
    landing_url_for,
    normalize_role,
    permission_required,
)


bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(landing_url_for(current_user))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.is_active and check_password_hash(user.password_hash, password):
            login_user(user)
            next_url = request.args.get("next")
            return redirect(next_url or landing_url_for(user))
        flash("用户名或密码错误。", "danger")

    return render_template("auth/login.html", page_title="登录")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/users", methods=["GET", "POST"])
@login_required
@permission_required("users.manage")
def users():
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "create":
            create_user()
        elif action == "update":
            update_user()
        elif action == "delete":
            delete_user()
        return redirect(url_for("auth.users"))

    users_list = scoped_users_query().order_by(User.created_at.desc()).all()
    admin_users = User.query.filter_by(role=ROLE_ADMIN, is_active=True).order_by(User.username).all()
    return render_template(
        "auth/users.html",
        page_title="用户管理",
        users=users_list,
        admin_users=admin_users,
        permission_groups=PERMISSION_GROUPS,
        role_labels=ROLE_LABELS,
        role_options=allowed_role_options(),
        default_role_permissions=DEFAULT_ROLE_PERMISSIONS,
    )


def scoped_users_query():
    query = User.query
    if current_user.normalized_role == ROLE_ADMIN:
        query = query.filter(User.role == ROLE_EMPLOYEE, User.parent_id == current_user.id)
    return query


def create_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = normalize_role(request.form.get("role", ROLE_EMPLOYEE))
    if role not in allowed_role_values():
        role = ROLE_EMPLOYEE

    if not username or not password:
        flash("用户名和密码不能为空。", "danger")
        return
    if User.query.filter_by(username=username).first():
        flash("用户名已存在。", "danger")
        return

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role=role,
        is_active=True,
    )
    apply_parent(user)
    apply_permissions(user, request.form.getlist("permissions"))
    db.session.add(user)
    db.session.commit()
    flash("用户已创建。", "success")


def update_user():
    user = User.query.get_or_404(int(request.form.get("user_id")))
    if not current_user.can_manage_user(user):
        flash("当前账号不能编辑该用户。", "warning")
        return

    role = normalize_role(request.form.get("role", user.role))
    if role not in allowed_role_values():
        role = user.normalized_role
    user.role = role
    user.is_active = request.form.get("is_active") == "1"
    apply_parent(user)
    apply_permissions(user, request.form.getlist("permissions"))

    password = request.form.get("password", "")
    if password:
        user.password_hash = generate_password_hash(password)
    db.session.commit()
    flash("用户已更新。", "success")


def delete_user():
    user = User.query.get_or_404(int(request.form.get("user_id")))
    if user.id == current_user.id:
        flash("不能删除当前登录用户。", "danger")
        return
    if not current_user.can_manage_user(user):
        flash("当前账号不能删除该用户。", "warning")
        return
    db.session.delete(user)
    db.session.commit()
    flash("用户已删除。", "success")


def apply_parent(user):
    if user.normalized_role != ROLE_EMPLOYEE:
        user.parent_id = None
        return
    if current_user.normalized_role == ROLE_ADMIN:
        user.parent_id = current_user.id
        return
    parent_id = request.form.get("parent_id") or None
    if parent_id:
        parent = User.query.get(int(parent_id))
        user.parent_id = parent.id if parent and parent.normalized_role == ROLE_ADMIN else None
    else:
        user.parent_id = None


def apply_permissions(user, selected_permissions):
    if user.normalized_role == ROLE_SUPER_ADMIN:
        user.permissions = None
        return
    permissions = selected_permissions or DEFAULT_ROLE_PERMISSIONS.get(user.normalized_role, [])
    if user.normalized_role == ROLE_ADMIN:
        permissions = [permission for permission in permissions if permission != "dashboard.view"]
    user.set_permissions(permissions)


def allowed_role_values():
    if current_user.is_super_admin():
        return {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_EMPLOYEE}
    if current_user.normalized_role == ROLE_ADMIN:
        return {ROLE_EMPLOYEE}
    return set()


def allowed_role_options():
    return [
        (role, ROLE_LABELS[role])
        for role in (ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_EMPLOYEE)
        if role in allowed_role_values()
    ]
