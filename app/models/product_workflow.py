import json
from datetime import datetime

from app.extensions import db


PRODUCT_METAFIELD_DEFINITIONS = (
    {"key": "product_developer", "label": "Product Developer"},
    {"key": "product_specialist", "label": "Product Specialist"},
    {"key": "elements", "label": "Elements"},
    {"key": "occasion", "label": "Occasion"},
    {"key": "recipient", "label": "Recipient"},
    {"key": "hobby", "label": "Hobby"},
)


class StoreConnection(db.Model):
    __tablename__ = "store_connections"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    platform = db.Column(db.String(20), nullable=False, index=True)
    shop_domain = db.Column(db.String(255), nullable=False)
    credential_type = db.Column(db.String(40), nullable=False)
    credentials_encrypted = db.Column(db.Text, nullable=False)
    oauth_access_token_encrypted = db.Column(db.Text)
    oauth_refresh_token_encrypted = db.Column(db.Text)
    token_expires_at = db.Column(db.DateTime)
    default_location_id = db.Column(db.String(255))
    currency = db.Column(db.String(12))
    connection_status = db.Column(db.String(20), nullable=False, default="untested", index=True)
    last_error = db.Column(db.Text)
    last_tested_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    drafts = db.relationship("StoreProductDraft", back_populates="store", lazy=True)
    __table_args__ = (db.UniqueConstraint("platform", "shop_domain", name="uq_store_connection_platform_domain"),)

    @property
    def platform_label(self):
        return {"shopify": "Shopify", "shoplazza": "Shoplazza"}.get(self.platform, self.platform)

    @property
    def status_label(self):
        return {"untested": "未测试", "connected": "连接成功", "failed": "连接失败"}.get(
            self.connection_status, self.connection_status
        )

    @property
    def status_badge(self):
        return {"connected": "success", "failed": "danger", "untested": "secondary"}.get(
            self.connection_status, "secondary"
        )


class ProductInboxItem(db.Model):
    __tablename__ = "product_inbox_items"
    id = db.Column(db.Integer, primary_key=True)
    source_product_id = db.Column(db.Integer, db.ForeignKey("competitor_products.id"), unique=True)
    source_domain = db.Column(db.String(255), nullable=False, index=True)
    source_url = db.Column(db.String(1000))
    product_type = db.Column(db.String(255))
    base_sku = db.Column(db.String(255))
    title = db.Column(db.String(500), nullable=False, index=True)
    description_html = db.Column(db.Text)
    tags_json = db.Column(db.Text)
    options_json = db.Column(db.Text)
    moved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    moved_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    images = db.relationship(
        "InboxProductImage", back_populates="item", cascade="all, delete-orphan",
        order_by="InboxProductImage.position"
    )
    variants = db.relationship(
        "InboxVariant", back_populates="item", cascade="all, delete-orphan",
        order_by="InboxVariant.position"
    )
    store_drafts = db.relationship("StoreProductDraft", back_populates="inbox_item", lazy=True)

    @property
    def tags(self):
        return _json_list(self.tags_json)

    @property
    def options(self):
        stored = _json_list(self.options_json)
        return stored or _derive_options(self.variants)

    @property
    def main_image(self):
        return self.images[0].source_url if self.images else ""

    @property
    def sku_summary(self):
        values = [variant.sku for variant in self.variants if variant.sku]
        return ", ".join(values[:3]) + (" …" if len(values) > 3 else "")

    @property
    def is_claimed(self):
        return bool(self.store_drafts)

    @property
    def claimed_store_ids(self):
        return {draft.store_connection_id for draft in self.store_drafts}


class InboxProductImage(db.Model):
    __tablename__ = "inbox_product_images"
    id = db.Column(db.Integer, primary_key=True)
    inbox_item_id = db.Column(db.Integer, db.ForeignKey("product_inbox_items.id"), nullable=False, index=True)
    source_url = db.Column(db.String(1200), nullable=False)
    local_path = db.Column(db.String(1000))
    alt_text = db.Column(db.String(500))
    position = db.Column(db.Integer, nullable=False, default=0)
    item = db.relationship("ProductInboxItem", back_populates="images")

    @property
    def public_url(self):
        return self.source_url

class InboxVariant(db.Model):
    __tablename__ = "inbox_variants"
    id = db.Column(db.Integer, primary_key=True)
    inbox_item_id = db.Column(db.Integer, db.ForeignKey("product_inbox_items.id"), nullable=False, index=True)
    option_values_json = db.Column(db.Text)
    sku = db.Column(db.String(255), nullable=False, index=True)
    price = db.Column(db.Numeric(12, 2))
    compare_at_price = db.Column(db.Numeric(12, 2))
    inventory_quantity = db.Column(db.Integer, nullable=False, default=0)
    image_url = db.Column(db.String(1200))
    local_image_path = db.Column(db.String(1000))
    available = db.Column(db.Boolean, nullable=False, default=True)
    weight_kg = db.Column(db.Numeric(10, 3))
    package_length_cm = db.Column(db.Numeric(10, 2))
    package_width_cm = db.Column(db.Numeric(10, 2))
    package_height_cm = db.Column(db.Numeric(10, 2))
    position = db.Column(db.Integer, nullable=False, default=0)
    item = db.relationship("ProductInboxItem", back_populates="variants")

    @property
    def option_values(self):
        return _json_dict(self.option_values_json)


class StoreProductDraft(db.Model):
    __tablename__ = "store_product_drafts"
    id = db.Column(db.Integer, primary_key=True)
    inbox_item_id = db.Column(db.Integer, db.ForeignKey("product_inbox_items.id"), nullable=False, index=True)
    store_connection_id = db.Column(db.Integer, db.ForeignKey("store_connections.id"), nullable=False, index=True)
    product_type = db.Column(db.String(255))
    base_sku = db.Column(db.String(255))
    title = db.Column(db.String(500), nullable=False, index=True)
    description_html = db.Column(db.Text)
    tags_json = db.Column(db.Text)
    options_json = db.Column(db.Text)
    product_metafields_json = db.Column(db.Text)
    sync_status = db.Column(db.String(30), nullable=False, default="local", index=True)
    has_pending_changes = db.Column(db.Boolean, nullable=False, default=True)
    remote_product_id = db.Column(db.String(255))
    remote_published = db.Column(db.Boolean, nullable=False, default=False)
    remote_handle = db.Column(db.String(500))
    remote_url = db.Column(db.String(1200))
    last_error = db.Column(db.Text)
    last_synced_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    inbox_item = db.relationship("ProductInboxItem", back_populates="store_drafts")
    store = db.relationship("StoreConnection", back_populates="drafts")
    images = db.relationship(
        "DraftProductImage", back_populates="draft", cascade="all, delete-orphan",
        order_by="DraftProductImage.position"
    )
    variants = db.relationship(
        "DraftVariant", back_populates="draft", cascade="all, delete-orphan",
        order_by="DraftVariant.position"
    )
    __table_args__ = (db.UniqueConstraint("inbox_item_id", "store_connection_id", name="uq_inbox_item_store"),)

    @property
    def tags(self):
        return _json_list(self.tags_json)

    @property
    def options(self):
        return _json_list(self.options_json)

    @property
    def product_metafields(self):
        return _json_dict(self.product_metafields_json)

    @property
    def main_image(self):
        return self.images[0].public_url if self.images else ""

    @property
    def sku_summary(self):
        values = [variant.sku for variant in self.variants if variant.sku]
        return ", ".join(values[:3]) + (" …" if len(values) > 3 else "")

    @property
    def status_label(self):
        labels = {
            "local": "本地待处理", "drafting": "草稿创建中", "remote_draft": "店铺草稿",
            "publishing": "发布中", "published": "发布成功", "failed": "发布失败"
        }
        if self.has_pending_changes and self.sync_status == "remote_draft":
            return "店铺草稿·有待同步修改"
        if self.has_pending_changes and self.sync_status == "published":
            return "发布成功·有待同步修改"
        return labels.get(self.sync_status, self.sync_status)

    @property
    def status_badge(self):
        return {
            "local": "secondary", "drafting": "info", "remote_draft": "warning",
            "publishing": "primary", "published": "success", "failed": "danger"
        }.get(self.sync_status, "secondary")


class DraftProductImage(db.Model):
    __tablename__ = "draft_product_images"
    id = db.Column(db.Integer, primary_key=True)
    draft_id = db.Column(db.Integer, db.ForeignKey("store_product_drafts.id"), nullable=False, index=True)
    source_url = db.Column(db.String(1200))
    local_path = db.Column(db.String(1000))
    public_url = db.Column(db.String(1200), nullable=False)
    remote_media_id = db.Column(db.String(255))
    alt_text = db.Column(db.String(500))
    position = db.Column(db.Integer, nullable=False, default=0)
    draft = db.relationship("StoreProductDraft", back_populates="images")


class DraftVariant(db.Model):
    __tablename__ = "draft_variants"
    id = db.Column(db.Integer, primary_key=True)
    draft_id = db.Column(db.Integer, db.ForeignKey("store_product_drafts.id"), nullable=False, index=True)
    option_values_json = db.Column(db.Text)
    sku = db.Column(db.String(255), nullable=False, index=True)
    price = db.Column(db.Numeric(12, 2))
    compare_at_price = db.Column(db.Numeric(12, 2))
    inventory_quantity = db.Column(db.Integer, nullable=False, default=0)
    image_url = db.Column(db.String(1200))
    local_image_path = db.Column(db.String(1000))
    remote_media_id = db.Column(db.String(255))
    weight_kg = db.Column(db.Numeric(10, 3))
    package_length_cm = db.Column(db.Numeric(10, 2))
    package_width_cm = db.Column(db.Numeric(10, 2))
    package_height_cm = db.Column(db.Numeric(10, 2))
    position = db.Column(db.Integer, nullable=False, default=0)
    draft = db.relationship("StoreProductDraft", back_populates="variants")

    @property
    def option_values(self):
        return _json_dict(self.option_values_json)


def _derive_options(variants):
    values_by_name = {}
    for variant in variants:
        for name, value in variant.option_values.items():
            values_by_name.setdefault(name, [])
            if value not in values_by_name[name]:
                values_by_name[name].append(value)
    return [
        {"name": name, "values": values}
        for name, values in values_by_name.items()
    ]

def _json_dict(raw):
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(raw):
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []
