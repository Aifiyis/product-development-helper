import queue
import threading
from datetime import datetime
from pathlib import Path

from app.extensions import db
from app.models import (
    DraftProductImage, DraftVariant, InboxProductImage, InboxVariant,
    StoreProductDraft,
)
from app.services.store_publish_service import redact_error_message, sync_store_product


_publish_queue = queue.Queue()
_publish_lock = threading.Lock()
_queued_drafts = set()
_running_drafts = set()
_worker = None


def enqueue_store_publish(draft_id, app, publish):
    global _worker
    with _publish_lock:
        if draft_id in _queued_drafts or draft_id in _running_drafts:
            return False
        _queued_drafts.add(draft_id)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(
                target=_publish_worker,
                args=(app,),
                daemon=True,
                name="store-product-publish-worker",
            )
            _worker.start()
    _publish_queue.put({"draft_id": draft_id, "publish": bool(publish)})
    return True


def run_store_publish_by_id(draft_id, app, publish):
    with app.app_context():
        draft = db.session.get(StoreProductDraft, draft_id)
        if not draft:
            return None
        try:
            result = sync_store_product(draft, publish=publish)
            draft.remote_product_id = result.get("remote_product_id") or draft.remote_product_id
            draft.remote_handle = result.get("remote_handle") or draft.remote_handle
            draft.remote_url = result.get("remote_url") or draft.remote_url
            cleanup_candidates = _apply_remote_media(draft, result, replace_urls=publish)
            draft.remote_published = bool(publish)
            draft.sync_status = "published" if publish else "remote_draft"
            draft.has_pending_changes = False
            draft.last_error = None
            draft.last_synced_at = datetime.utcnow()
            db.session.commit()
            if publish and cleanup_candidates:
                _delete_unreferenced_uploads(cleanup_candidates, app)
            return result
        except Exception as exc:
            db.session.rollback()
            draft = db.session.get(StoreProductDraft, draft_id)
            if draft:
                draft.sync_status = "failed"
                draft.has_pending_changes = True
                draft.last_error = redact_error_message(exc)[:2000]
                db.session.commit()
            return None


def _apply_remote_media(draft, result, replace_urls):
    cleanup_candidates = {}
    remote_images = result.get("remote_images") or []
    for position, image in enumerate(draft.images):
        remote = remote_images[position] if position < len(remote_images) else {}
        image.remote_media_id = remote.get("remote_media_id") or image.remote_media_id
        if replace_urls and remote.get("status") == "READY" and remote.get("url"):
            _remember_cleanup_candidate(
                cleanup_candidates, image.local_path, image.public_url
            )
            image.public_url = remote["url"]
            image.local_path = None

    remote_variants = result.get("remote_variant_images") or {}
    for variant in draft.variants:
        remote = remote_variants.get(variant.sku) or {}
        variant.remote_media_id = remote.get("remote_media_id") or variant.remote_media_id
        if replace_urls and remote.get("status") == "READY" and remote.get("url"):
            _remember_cleanup_candidate(
                cleanup_candidates, variant.local_image_path, variant.image_url
            )
            variant.image_url = remote["url"]
            variant.local_image_path = None
    return cleanup_candidates


def _remember_cleanup_candidate(candidates, local_path, public_url):
    if not local_path:
        return
    candidates.setdefault(local_path, set())
    if public_url:
        candidates[local_path].add(public_url)


def _delete_unreferenced_uploads(candidates, app):
    upload_root = (Path(app.instance_path) / "product_uploads").resolve()
    for relative_path, old_urls in candidates.items():
        if _upload_still_referenced(relative_path, old_urls):
            continue
        target = (upload_root / relative_path).resolve()
        if target == upload_root or upload_root not in target.parents:
            app.logger.warning("拒绝清理产品上传目录之外的文件：%s", relative_path)
            continue
        try:
            target.unlink(missing_ok=True)
            parent = target.parent
            while parent != upload_root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        except OSError as exc:
            app.logger.warning(
                "本地商品图片清理失败：%s (%s)", relative_path, type(exc).__name__
            )


def _upload_still_referenced(relative_path, old_urls):
    if DraftProductImage.query.filter_by(local_path=relative_path).first():
        return True
    if DraftVariant.query.filter_by(local_image_path=relative_path).first():
        return True
    for url in old_urls:
        if DraftProductImage.query.filter_by(public_url=url).first():
            return True
        if DraftVariant.query.filter_by(image_url=url).first():
            return True
        if InboxProductImage.query.filter_by(source_url=url).first():
            return True
        if InboxVariant.query.filter_by(image_url=url).first():
            return True
        if StoreProductDraft.query.filter(
            StoreProductDraft.description_html.contains(url)
        ).first():
            return True
    return False


def _publish_worker(app):
    while True:
        item = _publish_queue.get()
        draft_id = item["draft_id"]
        with _publish_lock:
            _queued_drafts.discard(draft_id)
            _running_drafts.add(draft_id)
        try:
            run_store_publish_by_id(draft_id, app, item["publish"])
        finally:
            with _publish_lock:
                _running_drafts.discard(draft_id)
            _publish_queue.task_done()
