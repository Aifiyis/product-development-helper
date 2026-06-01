from datetime import datetime

from app.extensions import db


PLATFORM_LABELS = {
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "tiktok": "tiktok",
    "instagram": "Instagram",
    "facebook": "facebook",
    "pinterest": "pinterest",
}


class CollectionTask(db.Model):
    __tablename__ = "collection_tasks"

    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(30), nullable=False, index=True)
    collection_platforms = db.Column(db.Text, nullable=True)
    product_keywords = db.Column(db.Text, nullable=False)
    comment_monitor_keywords = db.Column(db.Text, nullable=True)
    min_likes = db.Column(db.Integer, nullable=False, default=0)
    collection_cycle = db.Column(db.String(20), nullable=False, default="6h")
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_run_at = db.Column(db.DateTime, nullable=True)
    scheduler_job_id = db.Column(db.String(120), nullable=True)

    creator = db.relationship("User", back_populates="tasks")
    notes = db.relationship(
        "CollectedNote",
        back_populates="task",
        cascade="all, delete-orphan",
        lazy=True,
    )

    @property
    def keyword_list(self):
        return [item.strip() for item in self.product_keywords.splitlines() if item.strip()]

    @property
    def monitor_keyword_list(self):
        raw = self.comment_monitor_keywords or ""
        normalized = raw.replace(",", "\n").replace("，", "\n")
        return [item.strip() for item in normalized.splitlines() if item.strip()]

    @property
    def selected_platform_list(self):
        raw = self.collection_platforms or self.platform or "xiaohongshu"
        return [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]

    @property
    def selected_platform_labels(self):
        return [PLATFORM_LABELS.get(platform, platform) for platform in self.selected_platform_list]
