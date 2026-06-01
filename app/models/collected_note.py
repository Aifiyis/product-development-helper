from datetime import datetime

from app.extensions import db


class CollectedNote(db.Model):
    __tablename__ = "collected_notes"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("collection_tasks.id"), nullable=False)
    platform = db.Column(db.String(30), nullable=False, index=True)
    product_keyword = db.Column(db.String(255), nullable=True, index=True)
    title = db.Column(db.String(500), nullable=True)
    content = db.Column(db.Text, nullable=True)
    author = db.Column(db.String(255), nullable=True)
    likes_count = db.Column(db.Integer, nullable=False, default=0)
    comments_count = db.Column(db.Integer, nullable=False, default=0)
    publish_time = db.Column(db.DateTime, nullable=True)
    collection_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    source_url = db.Column(db.String(1000), nullable=True)
    triggered_comments = db.Column(db.Text, nullable=True)
    extra_data = db.Column(db.Text, nullable=True)

    task = db.relationship("CollectionTask", back_populates="notes")
