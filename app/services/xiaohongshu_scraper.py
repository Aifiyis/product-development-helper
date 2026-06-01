import json

from app.extensions import db
from app.models import CollectedNote
from app.services.scraper_base import ScraperBase, ScraperUnavailable


class XiaohongshuScraper(ScraperBase):
    search_url = "https://www.xiaohongshu.com/search_result?keyword={keyword}"

    def run_collection(self, task, platform=None):
        saved = 0
        for keyword in task.keyword_list:
            for raw_note in self.search_notes(task, keyword):
                if int(raw_note.get("likes_count") or 0) < task.min_likes:
                    continue
                normalized = self.normalize_note(task, keyword, raw_note, platform=platform)
                note = CollectedNote(
                    **{
                        **normalized,
                        "triggered_comments": json.dumps(
                            normalized.get("triggered_comments") or [],
                            ensure_ascii=False,
                        ),
                        "extra_data": json.dumps(
                            normalized.get("extra_data") or {},
                            ensure_ascii=False,
                        ),
                    }
                )
                db.session.add(note)
                saved += 1
        db.session.commit()
        return saved

    def search_notes(self, task, keyword):
        try:
            self.fetch(self.search_url.format(keyword=keyword), stealthy_headers=True)
        except ScraperUnavailable:
            return []
        except Exception:
            return []

        # Real Xiaohongshu extraction depends on authenticated browser state and selectors.
        return []
