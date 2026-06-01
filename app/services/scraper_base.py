from datetime import datetime


class ScraperUnavailable(RuntimeError):
    pass


class ScraperBase:
    fetcher_name = "StealthyFetcher"

    def __init__(self):
        self.fetcher = self._create_fetcher()

    def _create_fetcher(self):
        try:
            from scrapling.fetchers import StealthyFetcher
        except Exception:
            return None
        return StealthyFetcher

    def fetch(self, url, **kwargs):
        if self.fetcher is None:
            raise ScraperUnavailable("Scrapling is not installed or could not be imported.")
        return self.fetcher.fetch(url, **kwargs)

    def normalize_note(self, task, keyword, raw, platform=None):
        return {
            "task_id": task.id,
            "platform": platform or task.platform,
            "product_keyword": keyword,
            "title": raw.get("title"),
            "content": raw.get("content"),
            "author": raw.get("author"),
            "likes_count": int(raw.get("likes_count") or 0),
            "comments_count": int(raw.get("comments_count") or 0),
            "publish_time": raw.get("publish_time"),
            "collection_time": datetime.utcnow(),
            "source_url": raw.get("source_url"),
            "triggered_comments": raw.get("triggered_comments"),
            "extra_data": raw.get("extra_data"),
        }

    def run_collection(self, task):
        raise NotImplementedError
