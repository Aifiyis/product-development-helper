from datetime import datetime

from app.extensions import db


class CompetitorTask(db.Model):
    __tablename__ = "competitor_tasks"

    id = db.Column(db.Integer, primary_key=True)
    target_sites = db.Column(db.Text, nullable=False)
    target_category = db.Column(db.String(80), nullable=True)
    products_per_site = db.Column(db.Integer, nullable=False, default=20)
    fb_ad_threshold = db.Column(db.Integer, nullable=False, default=0)
    collection_cycle = db.Column(db.String(20), nullable=False, default="6h")
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    scheduler_job_id = db.Column(db.String(120), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_run_at = db.Column(db.DateTime, nullable=True)

    products = db.relationship(
        "CompetitorProduct",
        back_populates="task",
        cascade="all, delete-orphan",
        lazy=True,
    )

    @property
    def site_list(self):
        raw = self.target_sites or ""
        normalized = raw.replace("，", ",").replace("\n", ",")
        return [item.strip().lower().removeprefix("https://").removeprefix("http://").strip("/") for item in normalized.split(",") if item.strip()]
