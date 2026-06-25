from datetime import datetime
import json

from flask_login import UserMixin

from app.extensions import db, login_manager
from app.permissions import ALL_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS, ROLE_ADMIN, ROLE_EMPLOYEE, ROLE_SUPER_ADMIN, normalize_role


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_EMPLOYEE)
    permissions = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    tasks = db.relationship("CollectionTask", back_populates="creator", lazy=True)

    def is_admin(self):
        return self.normalized_role in {ROLE_SUPER_ADMIN, ROLE_ADMIN}

    @property
    def normalized_role(self):
        return normalize_role(self.role)

    def is_super_admin(self):
        return self.normalized_role == ROLE_SUPER_ADMIN

    def permission_list(self):
        if self.is_super_admin():
            return list(ALL_PERMISSIONS)
        if self.permissions:
            try:
                payload = json.loads(self.permissions)
                if isinstance(payload, list):
                    return [item for item in payload if item in ALL_PERMISSIONS]
            except json.JSONDecodeError:
                pass
        return list(DEFAULT_ROLE_PERMISSIONS.get(self.normalized_role, []))

    def set_permissions(self, permissions):
        allowed = [item for item in permissions if item in ALL_PERMISSIONS]
        self.permissions = json.dumps(sorted(set(allowed)), ensure_ascii=False)

    def can(self, permission):
        return self.is_super_admin() or permission in self.permission_list()

    def can_manage_user(self, target):
        if self.is_super_admin():
            return True
        if self.normalized_role == ROLE_ADMIN:
            return target.normalized_role == ROLE_EMPLOYEE
        return False


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
