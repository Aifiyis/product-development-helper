from datetime import datetime

from app.extensions import db


class CompetitorProduct(db.Model):
    __tablename__ = "competitor_products"

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("competitor_tasks.id"), nullable=True)
    source_domain = db.Column(db.String(255), nullable=False, index=True)
    source_type = db.Column(db.String(40), nullable=False, default="shopify_json")
    title = db.Column(db.String(500), nullable=True)
    price = db.Column(db.String(80), nullable=True)
    product_media = db.Column(db.Text, nullable=True)
    reviews_count = db.Column(db.Integer, nullable=False, default=0)
    variants = db.Column(db.Text, nullable=True)
    description = db.Column(db.Text, nullable=True)
    product_url = db.Column(db.String(1000), nullable=True)
    fb_ad_count = db.Column(db.Integer, nullable=True)
    matched_ad = db.Column(db.Text, nullable=True)
    collected_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    task = db.relationship("CompetitorTask", back_populates="products")
