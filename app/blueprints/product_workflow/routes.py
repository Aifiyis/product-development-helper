import json
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

import bleach
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (
    CompetitorProduct,
    DraftProductImage,
    DraftVariant,
    InboxProductImage,
    InboxVariant,
    ProductInboxItem,
    PRODUCT_METAFIELD_DEFINITIONS,
    StoreConnection,
    StoreProductDraft,
)
from app.permissions import permission_required
from app.services.credential_service import CredentialError, encrypt_credentials, normalize_shop_domain
from app.services.store_publish_queue import enqueue_store_publish
from app.services.store_publish_service import (
    StoreAPIError,
    adapter_for,
    redact_error_message,
    test_store_connection,
)


bp = Blueprint("product_workflow", __name__, url_prefix="/product-workflow")

ALLOWED_HTML_TAGS = [
    "p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "h1", "h2", "h3", "h4",
    "blockquote", "a", "img", "table", "thead", "tbody", "tr", "th", "td", "span", "div"
]
ALLOWED_HTML_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "*": ["class"],
}

def _sanitize_html(value):
    return bleach.clean(
        value or "",
        tags=ALLOWED_HTML_TAGS,
        attributes=ALLOWED_HTML_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )



@bp.get("/inbox")
@login_required
@permission_required("product_inbox.view")
def inbox():
    tab = request.args.get("tab", "unclaimed")
    title = request.args.get("title", "").strip()
    sku = request.args.get("sku", "").strip()
    store_id = request.args.get("store_id", type=int)
    status = request.args.get("status", "").strip()

    unclaimed_query = ProductInboxItem.query.filter(~ProductInboxItem.store_drafts.any())
    if title:
        unclaimed_query = unclaimed_query.filter(ProductInboxItem.title.ilike(f"%{title}%"))
    if sku:
        unclaimed_query = unclaimed_query.filter(ProductInboxItem.variants.any(InboxVariant.sku.ilike(f"%{sku}%")))
    unclaimed_items = unclaimed_query.order_by(ProductInboxItem.moved_at.desc()).limit(200).all()

    claimed_query = StoreProductDraft.query
    if title:
        claimed_query = claimed_query.filter(StoreProductDraft.title.ilike(f"%{title}%"))
    if sku:
        claimed_query = claimed_query.filter(StoreProductDraft.variants.any(DraftVariant.sku.ilike(f"%{sku}%")))
    if store_id:
        claimed_query = claimed_query.filter(StoreProductDraft.store_connection_id == store_id)
    if status:
        claimed_query = claimed_query.filter(StoreProductDraft.sync_status == status)
    claimed_drafts = claimed_query.order_by(StoreProductDraft.updated_at.desc()).limit(200).all()

    stores = StoreConnection.query.filter_by(is_active=True).order_by(StoreConnection.platform, StoreConnection.name).all()
    return render_template(
        "product_workflow/inbox.html",
        page_title="产品认领箱",
        tab=tab,
        unclaimed_items=unclaimed_items,
        claimed_drafts=claimed_drafts,
        stores=stores,
        filters={"title": title, "sku": sku, "store_id": store_id, "status": status},
    )


@bp.post("/inbox/move")
@login_required
@permission_required("product_inbox.move")
def move_to_inbox():
    product_ids = _int_list(request.form.getlist("product_ids"))
    single_id = request.form.get("product_id", type=int)
    if single_id:
        product_ids.append(single_id)
    product_ids = list(dict.fromkeys(product_ids))
    if not product_ids:
        flash("请至少选择一个产品。", "warning")
        return redirect(url_for("competitor.index"))

    error_reference = uuid.uuid4().hex[:8]
    for attempt in range(3):
        created = 0
        skipped = 0
        try:
            for product in CompetitorProduct.query.filter(CompetitorProduct.id.in_(product_ids)).all():
                if ProductInboxItem.query.filter_by(source_product_id=product.id).first():
                    skipped += 1
                    continue
                _create_inbox_snapshot(product)
                created += 1
            db.session.commit()
            break
        except IntegrityError as exc:
            db.session.rollback()
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
                continue
            current_app.logger.exception(
                "Move to product inbox failed after concurrent-write retries [ref=%s product_ids=%s]",
                error_reference, product_ids,
            )
            detail = f"{type(exc).__name__}: {exc}" if current_app.debug else f"错误编号：{error_reference}"
            flash(f"移入认领箱失败，可能存在重复提交。{detail}", "danger")
            return redirect(request.referrer or url_for("competitor.index"))
        except OperationalError as exc:
            db.session.rollback()
            is_locked = "database is locked" in str(exc).lower()
            if is_locked and attempt < 2:
                current_app.logger.warning(
                    "SQLite was busy while moving products to inbox; retrying [attempt=%s ref=%s product_ids=%s]",
                    attempt + 1, error_reference, product_ids,
                )
                time.sleep(0.2 * (attempt + 1))
                continue
            current_app.logger.exception(
                "Move to product inbox failed with database error [ref=%s product_ids=%s]",
                error_reference, product_ids,
            )
            detail = f"{type(exc).__name__}: {exc}" if current_app.debug else f"错误编号：{error_reference}"
            message = (
                "当前采集任务正在写入产品数据，认领操作需等待当前采集任务完成后再操作。"
                if is_locked else "移入认领箱失败，数据库暂时不可用。"
            )
            flash(f"{message}{detail}", "danger")
            return redirect(request.referrer or url_for("competitor.index"))
        except Exception as exc:
            db.session.rollback()
            current_app.logger.exception(
                "Move to product inbox failed [ref=%s product_ids=%s]",
                error_reference, product_ids,
            )
            detail = f"{type(exc).__name__}: {exc}" if current_app.debug else f"错误编号：{error_reference}"
            flash(f"移入认领箱失败。{detail}", "danger")
            return redirect(request.referrer or url_for("competitor.index"))
    flash(f"已移入 {created} 个产品，跳过 {skipped} 个已存在产品。", "success")
    return redirect(request.referrer or url_for("competitor.index"))


@bp.post("/inbox/<int:item_id>/remove")
@login_required
@permission_required("product_inbox.move")
def remove_from_inbox(item_id):
    item = db.get_or_404(ProductInboxItem, item_id)
    if item.store_drafts:
        flash("该产品已认领到店铺，不能直接移出认领箱。", "warning")
        return redirect(request.referrer or url_for("product_workflow.inbox", tab="claimed"))
    db.session.delete(item)
    db.session.commit()
    flash("产品已移出认领箱，可再次从产品抓取页面移入。", "success")
    return redirect(request.referrer or url_for("product_workflow.inbox", tab="unclaimed"))

@bp.route("/inbox/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("product_inbox.edit")
def edit_inbox_item(item_id):
    item = db.get_or_404(ProductInboxItem, item_id)
    if item.is_claimed:
        flash("该产品已被认领，请编辑对应店铺商品副本。", "warning")
        return redirect(url_for("product_workflow.inbox", tab="claimed"))
    if request.method == "POST":
        try:
            _update_inbox_item_from_form(item)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("product_workflow.edit_inbox_item", item_id=item.id))
        flash("未认领产品已保存。", "success")
        return redirect(url_for("product_workflow.edit_inbox_item", item_id=item.id))

    return render_template(
        "product_workflow/editor.html",
        page_title="编辑未认领产品",
        draft=item,
        editor_source_url=item.source_url,
        editor_variants=[_variant_for_editor(variant) for variant in item.variants],
        product_metafield_definitions=[],
        is_inbox_editor=True,
    )

@bp.post("/inbox/<int:item_id>/claim")
@login_required
@permission_required("product_inbox.claim")
def claim_item(item_id):
    item = db.get_or_404(ProductInboxItem, item_id)
    store_ids = _int_list(request.form.getlist("store_ids"))
    stores = StoreConnection.query.filter(StoreConnection.id.in_(store_ids), StoreConnection.is_active.is_(True)).all()
    if not stores:
        flash("请至少选择一家可用店铺。", "warning")
        return redirect(url_for("product_workflow.inbox"))

    created = 0
    skipped = 0
    for store in stores:
        exists = StoreProductDraft.query.filter_by(inbox_item_id=item.id, store_connection_id=store.id).first()
        if exists:
            skipped += 1
            continue
        _create_store_draft(item, store)
        created += 1
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("部分店铺已认领该产品，请刷新后重试。", "warning")
        return redirect(url_for("product_workflow.inbox", tab="claimed"))
    flash(f"已生成 {created} 个店铺商品副本，跳过 {skipped} 个重复认领。", "success")
    return redirect(url_for("product_workflow.inbox", tab="claimed"))


@bp.route("/drafts/<int:draft_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("product_inbox.edit")
def edit_draft(draft_id):
    draft = db.get_or_404(StoreProductDraft, draft_id)
    if request.method == "POST":
        try:
            _update_draft_from_form(draft)
            db.session.commit()
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("product_workflow.edit_draft", draft_id=draft.id))
        after_save = request.form.get("after_save", "save")
        if after_save == "draft":
            return _queue_draft_action(draft, publish=False)
        if after_save == "publish":
            return _queue_draft_action(draft, publish=True)
        flash("商品副本已保存。", "success")
        return redirect(url_for("product_workflow.edit_draft", draft_id=draft.id))

    return render_template(
        "product_workflow/editor.html",
        page_title="编辑店铺商品",
        draft=draft,
        editor_source_url=draft.inbox_item.source_url,
        is_inbox_editor=False,
        editor_variants=[_variant_for_editor(item) for item in draft.variants],
        product_metafield_definitions=PRODUCT_METAFIELD_DEFINITIONS,
        editor_metafield_values=_metafield_form_values(draft),
        shopify_reference_url=(
            url_for("product_workflow.shopify_reference_data", draft_id=draft.id)
            if draft.store.platform == "shopify" else ""
        ),
        shopify_category_url=(
            url_for("product_workflow.shopify_category_search", draft_id=draft.id)
            if draft.store.platform == "shopify" else ""
        ),

    )


@bp.get("/drafts/<int:draft_id>/shopify-reference-data")
@login_required
@permission_required("product_inbox.edit")
def shopify_reference_data(draft_id):
    draft = db.get_or_404(StoreProductDraft, draft_id)
    if draft.store.platform != "shopify":
        return jsonify({"error": "该店铺不是 Shopify 店铺。"}), 400
    try:
        return jsonify(adapter_for(draft.store).editor_reference_data())
    except StoreAPIError as exc:
        return jsonify({"error": redact_error_message(exc)}), 502


@bp.get("/drafts/<int:draft_id>/shopify-categories")
@login_required
@permission_required("product_inbox.edit")
def shopify_category_search(draft_id):
    draft = db.get_or_404(StoreProductDraft, draft_id)
    if draft.store.platform != "shopify":
        return jsonify({"error": "该店铺不是 Shopify 店铺。"}), 400
    adapter = adapter_for(draft.store)
    try:
        if request.args.get("recommend") == "1":
            categories = adapter.suggest_product_categories(
                request.args.get("title") or draft.title, first=8
            )
        else:
            categories = adapter.search_product_categories(request.args.get("q", ""), first=8)
        return jsonify({"categories": categories})
    except StoreAPIError as exc:
        return jsonify({"error": redact_error_message(exc)}), 502


@bp.post("/drafts/<int:draft_id>/create-remote-draft")
@login_required
@permission_required("product_inbox.create_draft")
def create_remote_draft(draft_id):
    return _queue_draft_action(db.get_or_404(StoreProductDraft, draft_id), publish=False)


@bp.post("/drafts/<int:draft_id>/publish")
@login_required
@permission_required("product_inbox.publish")
def publish_draft(draft_id):
    return _queue_draft_action(db.get_or_404(StoreProductDraft, draft_id), publish=True)


@bp.get("/drafts/<int:draft_id>/status")
@login_required
@permission_required("product_inbox.view")
def draft_status(draft_id):
    draft = db.get_or_404(StoreProductDraft, draft_id)
    return jsonify({
        "id": draft.id,
        "status": draft.sync_status,
        "status_label": draft.status_label,
        "status_badge": draft.status_badge,
        "has_pending_changes": draft.has_pending_changes,
        "remote_url": draft.remote_url or "",
        "last_error": draft.last_error or "",
    })


@bp.get("/stores")
@login_required
@permission_required("stores.view")
def stores():
    rows = StoreConnection.query.order_by(StoreConnection.platform, StoreConnection.name).all()
    return render_template("product_workflow/stores.html", page_title="店铺管理", stores=rows)


@bp.post("/stores")
@login_required
@permission_required("stores.manage")
def create_store():
    platform = request.form.get("platform", "").strip().lower()
    domain = normalize_shop_domain(request.form.get("shop_domain"))
    error = _validate_store_input(platform, domain)
    if error:
        flash(error, "danger")
        return redirect(url_for("product_workflow.stores"))
    try:
        credentials = _credentials_from_form(platform)
        encrypted_credentials = encrypt_credentials(credentials) if credentials else None
    except CredentialError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("product_workflow.stores"))
    if not credentials:
        flash("请填写完整店铺凭据。", "danger")
        return redirect(url_for("product_workflow.stores"))
    store = StoreConnection(
        name=request.form.get("name", "").strip() or domain,
        platform=platform,
        shop_domain=domain,
        credential_type="shopify_client_credentials" if platform == "shopify" else "shoplazza_private_token",
        credentials_encrypted=encrypted_credentials,
        created_by=current_user.id,
    )
    db.session.add(store)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("该平台和域名已经配置。", "warning")
        return redirect(url_for("product_workflow.stores"))
    flash("店铺已添加，请执行连接测试。", "success")
    return redirect(url_for("product_workflow.stores"))


@bp.post("/stores/<int:store_id>/edit")
@login_required
@permission_required("stores.manage")
def edit_store(store_id):
    store = db.get_or_404(StoreConnection, store_id)
    domain = normalize_shop_domain(request.form.get("shop_domain"))
    error = _validate_store_input(store.platform, domain)
    if error:
        flash(error, "danger")
        return redirect(url_for("product_workflow.stores"))
    store.name = request.form.get("name", "").strip() or domain
    store.shop_domain = domain
    store.default_location_id = request.form.get("default_location_id", "").strip() or None
    try:
        credentials = _credentials_from_form(store.platform, allow_blank=True)
        if credentials:
            store.credentials_encrypted = encrypt_credentials(credentials)
            store.oauth_access_token_encrypted = None
            store.token_expires_at = None
            store.connection_status = "untested"
    except CredentialError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("product_workflow.stores"))
    db.session.commit()
    flash("店铺配置已更新。", "success")
    return redirect(url_for("product_workflow.stores"))


@bp.post("/stores/<int:store_id>/test")
@login_required
@permission_required("stores.manage")
def test_store(store_id):
    store = db.get_or_404(StoreConnection, store_id)
    try:
        result = test_store_connection(store)
        flash(f"连接成功：{result.get('name') or store.name}", "success")
    except Exception as exc:
        db.session.rollback()
        store = db.session.get(StoreConnection, store_id)
        store.connection_status = "failed"
        store.last_error = redact_error_message(exc)[:2000]
        db.session.commit()
        flash(f"连接失败：{store.last_error}", "danger")
    return redirect(url_for("product_workflow.stores"))


@bp.post("/stores/<int:store_id>/toggle")
@login_required
@permission_required("stores.manage")
def toggle_store(store_id):
    store = db.get_or_404(StoreConnection, store_id)
    store.is_active = not store.is_active
    db.session.commit()
    flash("店铺已启用。" if store.is_active else "店铺已停用。", "success")
    return redirect(url_for("product_workflow.stores"))


@bp.get("/uploads/<path:filename>")
def uploaded_file(filename):
    upload_root = Path(current_app.instance_path) / "product_uploads"
    return send_from_directory(upload_root, filename)


def _queue_draft_action(draft, publish):
    permission = "product_inbox.publish" if publish else "product_inbox.create_draft"
    if not current_user.can(permission):
        flash("当前账号没有执行该操作的权限。", "danger")
        return redirect(url_for("product_workflow.edit_draft", draft_id=draft.id))
    if not publish and draft.remote_published:
        flash("该商品已正式发布，不能再移入店铺草稿；请使用更新发布。", "warning")
        return redirect(url_for("product_workflow.inbox", tab="claimed"))

    target_status = "publishing" if publish else "drafting"
    updated = StoreProductDraft.query.filter(
        StoreProductDraft.id == draft.id,
        ~StoreProductDraft.sync_status.in_(["drafting", "publishing"]),
    ).update(
        {StoreProductDraft.sync_status: target_status, StoreProductDraft.last_error: None},
        synchronize_session=False,
    )
    db.session.commit()
    if updated != 1:
        flash("该商品正在处理中，请勿重复提交。", "warning")
        return redirect(url_for("product_workflow.inbox", tab="claimed"))

    queued = enqueue_store_publish(draft.id, current_app._get_current_object(), publish=publish)
    if not queued:
        flash("该商品已经在发布队列中。", "warning")
    else:
        flash("发布任务已提交。" if publish else "店铺草稿任务已提交。", "success")
    return redirect(url_for("product_workflow.inbox", tab="claimed"))

def _create_inbox_snapshot(product):
    item = ProductInboxItem(
        source_product_id=product.id,
        source_domain=product.source_domain,
        source_url=product.product_url,
        product_type="",
        base_sku=f"PDH-{product.id}",
        title=product.title or f"未命名产品 {product.id}",
        description_html=_sanitize_html(product.description),
        tags_json=product.product_tags or "[]",
        moved_by=current_user.id,
    )
    db.session.add(item)
    media = _json_value(product.product_media, {})
    image_urls = []
    for url in [media.get("main")] + list(media.get("carousel") or []):
        if url and url not in image_urls:
            image_urls.append(url)
    for position, url in enumerate(image_urls):
        item.images.append(InboxProductImage(source_url=url, alt_text=item.title, position=position))

    raw_variants = _json_value(product.variants, [])
    if not raw_variants:
        raw_variants = [{"title": "默认", "price": product.price, "available": True}]
    for position, raw in enumerate(raw_variants):
        option_values = _variant_option_values(raw, position, len(raw_variants))
        sku = (raw.get("sku") or "").strip() or f"PDH-{product.id}-{position + 1:03d}"
        item.variants.append(InboxVariant(
            option_values_json=json.dumps(option_values, ensure_ascii=False),
            sku=sku,
            price=_decimal(raw.get("price") or product.price),
            compare_at_price=_decimal(raw.get("compare_at_price")),
            inventory_quantity=_integer(raw.get("inventory_quantity"), 0),
            image_url=raw.get("image") or "",
            available=raw.get("available") is not False,
            weight_kg=_decimal(raw.get("weight_kg")),
            position=position,
        ))
    item.options_json = json.dumps(_derive_options(item.variants), ensure_ascii=False)
    return item


def _create_store_draft(item, store):
    options = item.options
    draft = StoreProductDraft(
        inbox_item=item,
        store=store,
        product_type=item.product_type,
        base_sku=item.base_sku,
        title=item.title,
        description_html=_sanitize_html(item.description_html),
        tags_json=item.tags_json,
        options_json=json.dumps(options, ensure_ascii=False),
        created_by=current_user.id,
    )
    db.session.add(draft)
    for image in item.images:
        draft.images.append(DraftProductImage(
            source_url=image.source_url,
            public_url=image.source_url,
            local_path=image.local_path,
            alt_text=image.alt_text,
            position=image.position,
        ))
    for variant in item.variants:
        draft.variants.append(DraftVariant(
            option_values_json=variant.option_values_json,
            sku=variant.sku,
            price=variant.price,
            compare_at_price=variant.compare_at_price,
            inventory_quantity=variant.inventory_quantity,
            image_url=variant.image_url,
            local_image_path=variant.local_image_path,
            weight_kg=variant.weight_kg,
            package_length_cm=variant.package_length_cm,
            package_width_cm=variant.package_width_cm,
            package_height_cm=variant.package_height_cm,
            position=variant.position,
        ))
    return draft


def _update_inbox_item_from_form(item):
    item.title = request.form.get("title", "").strip()
    item.product_type = request.form.get("product_type", "").strip()
    item.base_sku = request.form.get("base_sku", "").strip()
    item.description_html = _sanitize_html(request.form.get("description_html", ""))
    tags = [value.strip() for value in request.form.get("tags", "").split(",") if value.strip()]
    item.tags_json = json.dumps(tags, ensure_ascii=False)
    options = _normalize_options(_json_value(request.form.get("options_json"), []))
    item.options_json = json.dumps(options, ensure_ascii=False)
    option_names = [option["name"] for option in options]

    existing_variants = {variant.id: variant for variant in item.variants}
    retained_ids = set()
    count = _integer(request.form.get("variant_count"), 0)
    for index in range(count):
        variant_id = _integer(request.form.get(f"variant_id-{index}"), 0)
        variant = existing_variants.get(variant_id) or InboxVariant(item=item)
        if variant.id:
            retained_ids.add(variant.id)
        variant.option_values_json = json.dumps(
            _variant_values_for_options(
                _json_value(request.form.get(f"variant_options-{index}"), {}), option_names
            ),
            ensure_ascii=False,
        )
        variant.sku = request.form.get(f"variant_sku-{index}", "").strip()
        variant.price = _decimal(request.form.get(f"variant_price-{index}"))
        variant.compare_at_price = _decimal(request.form.get(f"variant_compare_at-{index}"))
        variant.inventory_quantity = _integer(request.form.get(f"variant_inventory-{index}"), 0)
        variant.weight_kg = _decimal(request.form.get(f"variant_weight-{index}"))
        variant.package_length_cm = _decimal(request.form.get(f"variant_length-{index}"))
        variant.package_width_cm = _decimal(request.form.get(f"variant_width-{index}"))
        variant.package_height_cm = _decimal(request.form.get(f"variant_height-{index}"))
        variant.position = index
        upload = request.files.get(f"variant_image-{index}")
        if upload and upload.filename:
            public_url, local_path = _save_upload(f"inbox-{item.id}", upload)
            variant.image_url = public_url
            variant.local_image_path = local_path
    for variant_id, variant in existing_variants.items():
        if variant_id not in retained_ids:
            db.session.delete(variant)

    for image in list(item.images):
        if request.form.get(f"remove_image-{image.id}") == "1":
            db.session.delete(image)
            continue
        image.position = _integer(request.form.get(f"image_position-{image.id}"), image.position)
        image.alt_text = request.form.get(f"image_alt-{image.id}", image.alt_text or "").strip()
    next_position = max([image.position for image in item.images] + [-1]) + 1
    for upload in request.files.getlist("product_images"):
        if not upload or not upload.filename:
            continue
        public_url, local_path = _save_upload(f"inbox-{item.id}", upload)
        item.images.append(InboxProductImage(
            source_url=public_url,
            local_path=local_path,
            alt_text=item.title,
            position=next_position,
        ))
        next_position += 1

def _update_draft_from_form(draft):
    draft.title = request.form.get("title", "").strip()
    draft.product_type = request.form.get("product_type", "").strip()
    draft.category_id = request.form.get("category_id", "").strip()
    draft.category_name = request.form.get("category_name", "").strip()
    if draft.category_name and not draft.category_id:
        raise ValueError("请从搜索或推荐结果中选择 Category，不能只填写分类名称。")
    draft.base_sku = request.form.get("base_sku", "").strip()
    draft.description_html = _sanitize_html(request.form.get("description_html", ""))
    tags = [item.strip() for item in request.form.get("tags", "").split(",") if item.strip()]
    draft.tags_json = json.dumps(tags, ensure_ascii=False)
    draft.product_metafields_json = json.dumps({
        definition["key"]: request.form.get(
            f'metafield_{definition["key"]}', ""
        ).strip()
        for definition in PRODUCT_METAFIELD_DEFINITIONS
        if request.form.get(f'metafield_{definition["key"]}', "").strip()
    }, ensure_ascii=False)

    options = _normalize_options(_json_value(request.form.get("options_json"), []))
    draft.options_json = json.dumps(options, ensure_ascii=False)
    option_names = [option["name"] for option in options]

    existing_variants = {item.id: item for item in draft.variants}
    retained_ids = set()
    count = _integer(request.form.get("variant_count"), 0)
    for index in range(count):
        variant_id = _integer(request.form.get(f"variant_id-{index}"), 0)
        variant = existing_variants.get(variant_id) or DraftVariant(draft=draft)
        if variant.id:
            retained_ids.add(variant.id)
        variant.option_values_json = json.dumps(
            _variant_values_for_options(
                _json_value(request.form.get(f"variant_options-{index}"), {}), option_names
            ),
            ensure_ascii=False,
        )
        variant.sku = request.form.get(f"variant_sku-{index}", "").strip()
        variant.price = _decimal(request.form.get(f"variant_price-{index}"))
        variant.compare_at_price = _decimal(request.form.get(f"variant_compare_at-{index}"))
        variant.inventory_quantity = _integer(request.form.get(f"variant_inventory-{index}"), 0)
        variant.weight_kg = _decimal(request.form.get(f"variant_weight-{index}"))
        variant.package_length_cm = _decimal(request.form.get(f"variant_length-{index}"))
        variant.package_width_cm = _decimal(request.form.get(f"variant_width-{index}"))
        variant.package_height_cm = _decimal(request.form.get(f"variant_height-{index}"))
        variant.position = index
        upload = request.files.get(f"variant_image-{index}")
        if upload and upload.filename:
            public_url, local_path = _save_upload(draft.id, upload)
            variant.image_url = public_url
            variant.local_image_path = local_path
    for variant_id, variant in existing_variants.items():
        if variant_id not in retained_ids:
            db.session.delete(variant)

    for image in list(draft.images):
        if request.form.get(f"remove_image-{image.id}") == "1":
            db.session.delete(image)
            continue
        image.position = _integer(request.form.get(f"image_position-{image.id}"), image.position)
        image.alt_text = request.form.get(f"image_alt-{image.id}", image.alt_text or "").strip()
    next_position = max([image.position for image in draft.images] + [-1]) + 1
    for upload in request.files.getlist("product_images"):
        if not upload or not upload.filename:
            continue
        public_url, local_path = _save_upload(draft.id, upload)
        draft.images.append(DraftProductImage(
            public_url=public_url,
            local_path=local_path,
            alt_text=draft.title,
            position=next_position,
        ))
        next_position += 1

    if draft.sync_status == "failed":
        if draft.remote_published:
            draft.sync_status = "published"
        else:
            draft.sync_status = "remote_draft" if draft.remote_product_id else "local"
    draft.has_pending_changes = True
    draft.last_error = None


def _save_upload(draft_id, upload):
    extension = upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    if extension not in current_app.config.get("PRODUCT_UPLOAD_EXTENSIONS", set()):
        raise ValueError("仅支持 JPG、PNG 和 WebP 图片。")
    safe_name = secure_filename(upload.filename) or f"image.{extension}"
    relative = f"{draft_id}/{uuid.uuid4().hex}_{safe_name}"
    target = Path(current_app.instance_path) / "product_uploads" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    upload.save(target)
    base_url = current_app.config.get("PUBLIC_BASE_URL") or request.url_root.rstrip("/")
    public_url = f"{base_url}{url_for('product_workflow.uploaded_file', filename=relative)}"
    return public_url, relative


def _metafield_form_values(draft):
    values = draft.product_metafields
    return {
        definition["key"]: (
            values.get(definition["key"])
            or values.get(definition.get("legacy_key"), "")
            or ""
        )
        for definition in PRODUCT_METAFIELD_DEFINITIONS
    }


def _variant_for_editor(variant):
    return {
        "id": variant.id,
        "options": variant.option_values,
        "sku": variant.sku,
        "price": str(variant.price or ""),
        "compare_at_price": str(variant.compare_at_price or ""),
        "inventory_quantity": variant.inventory_quantity,
        "image_url": variant.image_url or "",
        "weight_kg": str(variant.weight_kg or ""),
        "package_length_cm": str(variant.package_length_cm or ""),
        "package_width_cm": str(variant.package_width_cm or ""),
        "package_height_cm": str(variant.package_height_cm or ""),
    }


def _normalize_options(raw_options):
    return [
        {"name": str(option.get("name") or "").strip(), "values": [
            str(value).strip() for value in option.get("values") or [] if str(value).strip()
        ]}
        for option in raw_options
        if isinstance(option, dict) and str(option.get("name") or "").strip()
    ]


def _variant_values_for_options(raw_values, option_names):
    if not isinstance(raw_values, dict):
        return {}
    return {
        name: str(raw_values.get(name)).strip()
        for name in option_names
        if raw_values.get(name) not in (None, "") and str(raw_values.get(name)).strip()
    }


def _derive_options(variants):
    values_by_name = {}
    for variant in variants:
        for name, value in variant.option_values.items():
            values_by_name.setdefault(name, [])
            if value not in values_by_name[name]:
                values_by_name[name].append(value)
    return [{"name": name, "values": values} for name, values in values_by_name.items()]


def _variant_option_values(raw, position, total):
    direct = raw.get("option_values")
    if isinstance(direct, dict):
        return {str(key): str(value) for key, value in direct.items() if value not in (None, "")}
    options = raw.get("options")
    if isinstance(options, dict):
        return {str(key): str(value) for key, value in options.items() if value not in (None, "")}
    title = str(raw.get("title") or "").strip()
    if total > 1 and title and title.lower() != "default title":
        return {"变体": title}
    return {}


def _credentials_from_form(platform, allow_blank=False):
    if platform == "shopify":
        client_id = request.form.get("client_id", "").strip()
        client_secret = request.form.get("client_secret", "").strip()
        if allow_blank and not client_id and not client_secret:
            return None
        if bool(client_id) != bool(client_secret):
            raise CredentialError("Shopify Client ID 和 Client Secret 必须同时填写。")
        return {"client_id": client_id, "client_secret": client_secret} if client_id and client_secret else None
    token = request.form.get("access_token", "").strip()
    if allow_blank and not token:
        return None
    return {"access_token": token} if token else None


def _validate_store_input(platform, domain):
    if platform not in {"shopify", "shoplazza"}:
        return "请选择有效的平台。"
    if not domain:
        return "店铺域名不能为空。"
    if platform == "shopify" and not domain.endswith(".myshopify.com"):
        return "Shopify 请填写 .myshopify.com 店铺域名。"
    if platform == "shoplazza" and not (
        domain.endswith(".myshoplaza.com") or domain.endswith(".myshoplazza.com")
    ):
        return "Shoplazza 请填写 .myshoplaza.com 店铺域名。"
    return None


def _json_value(raw, default):
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw or json.dumps(default))
    except (json.JSONDecodeError, TypeError):
        return default


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _int_list(values):
    result = []
    for value in values:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result
