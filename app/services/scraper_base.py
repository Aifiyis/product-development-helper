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
            return StealthyFetcher
        except Exception:
            try:
                from scrapling.fetchers import Fetcher
                return Fetcher
            except Exception:
                return None

    def fetch(self, url, **kwargs):
        if self.fetcher is None:
            raise ScraperUnavailable("Scrapling is not installed or could not be imported.")
        if hasattr(self.fetcher, "fetch"):
            return self.fetcher.fetch(url, **kwargs)
        if hasattr(self.fetcher, "get"):
            return self.fetcher.get(url, **kwargs)
        raise ScraperUnavailable("Scrapling fetcher does not expose a supported fetch method.")

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
