import json
import queue
import threading
from datetime import datetime

from app.extensions import db, scheduler
from app.models import CollectionTask, CompetitorTask
from app.services.competitor_scraper import CompetitorScraper
from app.services.douyin_scraper import DouyinScraper
from app.services.xiaohongshu_scraper import XiaohongshuScraper


SCRAPER_PLATFORMS = {
    "xiaohongshu": XiaohongshuScraper,
    "douyin": DouyinScraper,
}

_competitor_queue = queue.Queue()
_competitor_queue_lock = threading.Lock()
_competitor_worker = None
_queued_competitor_tasks = set()
_running_competitor_tasks = set()


def parse_cycle(cycle):
    value = (cycle or "").strip().lower()
    if value.endswith("m") and value[:-1].isdigit():
        return {"minutes": max(1, int(value[:-1]))}
    if value.endswith("h") and value[:-1].isdigit():
        return {"hours": max(1, int(value[:-1]))}
    if value.endswith("d") and value[:-1].isdigit():
        return {"days": max(1, int(value[:-1]))}
    return {"hours": 6}


def scraper_for(platform):
    scraper_class = SCRAPER_PLATFORMS.get(platform)
    return scraper_class() if scraper_class else None


def run_task_by_id(task_id, app):
    with app.app_context():
        task = CollectionTask.query.get(task_id)
        if not task or task.status != "active":
            return 0
        target_platforms = task.selected_platform_list if task.platform == "platform" else [task.platform]
        saved = 0
        for platform in target_platforms:
            scraper = scraper_for(platform)
            if scraper:
                saved += scraper.run_collection(task, platform=platform)
        task.last_run_at = datetime.utcnow()
        db.session.commit()
        return saved


def register_task_job(task, app):
    job_id = task.scheduler_job_id or f"{task.platform}_collection_task_{task.id}"
    existing = scheduler.get_job(job_id)
    if existing:
        scheduler.remove_job(job_id)

    scheduler.add_job(
        id=job_id,
        func=run_task_by_id,
        trigger="interval",
        kwargs={"task_id": task.id, "app": app},
        replace_existing=True,
        **parse_cycle(task.collection_cycle),
    )
    task.scheduler_job_id = job_id
    db.session.commit()
    return job_id


def run_competitor_task_by_id(task_id, app, complete_instant=False):
    with app.app_context():
        task = CompetitorTask.query.get(task_id)
        if not task or not task.is_runnable:
            return 0
        task.status = "collecting"
        task.last_error = None
        db.session.commit()

        scraper = CompetitorScraper()
        saved = scraper.run_collection(task)
        errors = getattr(scraper, "errors", [])
        task.last_run_at = datetime.utcnow()
        task.last_error = "\n".join(errors[:20]) if errors else None
        task.last_run_summary = json.dumps(
            {
                "saved": saved,
                "errors": errors[:20],
                "sites": task.site_list,
            },
            ensure_ascii=False,
        )
        if errors and saved == 0:
            task.status = "failed"
        elif complete_instant or task.collection_cycle == "instant":
            task.status = "completed"
        else:
            task.status = "collecting"
        db.session.commit()
        return saved


def enqueue_competitor_task(task_id, app, complete_instant=False):
    global _competitor_worker
    with _competitor_queue_lock:
        if task_id in _queued_competitor_tasks or task_id in _running_competitor_tasks:
            return False
        _queued_competitor_tasks.add(task_id)
        if _competitor_worker is None or not _competitor_worker.is_alive():
            _competitor_worker = threading.Thread(
                target=_competitor_queue_worker,
                args=(app,),
                daemon=True,
                name="competitor-collection-worker",
            )
            _competitor_worker.start()
    with app.app_context():
        task = CompetitorTask.query.get(task_id)
        if task and task.status in {"active", "collecting"}:
            task.status = "queued"
            task.last_error = None
            db.session.commit()
    _competitor_queue.put({"task_id": task_id, "complete_instant": complete_instant})
    return True


def _competitor_queue_worker(app):
    while True:
        item = _competitor_queue.get()
        task_id = item["task_id"]
        with _competitor_queue_lock:
            _queued_competitor_tasks.discard(task_id)
            _running_competitor_tasks.add(task_id)
        try:
            run_competitor_task_by_id(task_id, app, complete_instant=item.get("complete_instant", False))
        finally:
            with _competitor_queue_lock:
                _running_competitor_tasks.discard(task_id)
            _competitor_queue.task_done()


def register_competitor_job(task, app):
    if task.collection_cycle == "instant":
        task.scheduler_job_id = None
        db.session.commit()
        return None
    job_id = task.scheduler_job_id or f"competitor_task_{task.id}"
    existing = scheduler.get_job(job_id)
    if existing:
        scheduler.remove_job(job_id)

    scheduler.add_job(
        id=job_id,
        func=enqueue_competitor_task,
        trigger="interval",
        kwargs={"task_id": task.id, "app": app},
        replace_existing=True,
        **parse_cycle(task.collection_cycle),
    )
    task.scheduler_job_id = job_id
    db.session.commit()
    return job_id


def restore_active_jobs(app):
    active_tasks = CollectionTask.query.filter_by(status="active").all()
    for task in active_tasks:
        register_task_job(task, app)
    active_competitor_tasks = CompetitorTask.query.filter(CompetitorTask.status.in_(["active", "collecting"])).all()
    for task in active_competitor_tasks:
        if task.collection_cycle != "instant":
            register_competitor_job(task, app)
