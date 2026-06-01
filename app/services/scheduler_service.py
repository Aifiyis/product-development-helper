from datetime import datetime

from app.extensions import db, scheduler
from app.models import CollectionTask
from app.services.douyin_scraper import DouyinScraper
from app.services.xiaohongshu_scraper import XiaohongshuScraper


SCRAPER_PLATFORMS = {
    "xiaohongshu": XiaohongshuScraper,
    "douyin": DouyinScraper,
}


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


def restore_active_jobs(app):
    active_tasks = CollectionTask.query.filter_by(status="active").all()
    for task in active_tasks:
        register_task_job(task, app)
