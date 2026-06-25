from datetime import datetime

from app.extensions import db


class CompetitorTask(db.Model):
    __tablename__ = "competitor_tasks"

    id = db.Column(db.Integer, primary_key=True)
    target_sites = db.Column(db.Text, nullable=False)
    target_category = db.Column(db.String(80), nullable=True)
    product_keywords = db.Column(db.Text, nullable=True)
    sort_mode = db.Column(db.String(40), nullable=False, default="best_selling")
    products_per_site = db.Column(db.Integer, nullable=False, default=20)
    fb_ad_threshold = db.Column(db.Integer, nullable=False, default=0)
    collection_cycle = db.Column(db.String(20), nullable=False, default="6h")
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    scheduler_job_id = db.Column(db.String(120), nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    last_run_summary = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_run_at = db.Column(db.DateTime, nullable=True)

    products = db.relationship(
        "CompetitorProduct",
        back_populates="task",
        lazy=True,
    )

    @property
    def site_list(self):
        raw = self.target_sites or ""
        normalized = raw.replace("，", ",").replace("\n", ",")
        return [item.strip().lower().removeprefix("https://").removeprefix("http://").strip("/") for item in normalized.split(",") if item.strip()]

    @property
    def keyword_list(self):
        raw = self.product_keywords or ""
        normalized = raw.replace("，", ",").replace("\n", ",")
        return [item.strip().lower() for item in normalized.split(",") if item.strip()]

    @property
    def is_runnable(self):
        return self.status in {"active", "queued", "collecting"}

    @property
    def status_label(self):
        labels = {
            "active": "采集中",
            "queued": "采集中",
            "collecting": "采集中",
            "completed": "已完成",
            "failed": "失败",
            "paused": "已暂停",
        }
        return labels.get(self.status, self.status)

    @property
    def status_badge(self):
        badges = {
            "active": "primary",
            "queued": "primary",
            "collecting": "primary",
            "completed": "success",
            "failed": "danger",
            "paused": "secondary",
        }
        return badges.get(self.status, "secondary")
