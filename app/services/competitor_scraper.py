import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from html import unescape

from app.extensions import db
from app.models import CompetitorProduct
from app.services.fb_ads_service import collect_fb_ads
from app.services.scraper_base import ScraperBase


class CompetitorScraper(ScraperBase):
    def run_collection(self, task):
        saved = 0
        for domain in task.site_list:
            products = self.fetch_shopify_products(domain, task.products_per_site)
            ads = collect_fb_ads(domain, task.fb_ad_threshold)
            for raw in products:
                product = CompetitorProduct(
                    task_id=task.id,
                    source_domain=domain,
                    source_type=raw.get("source_type", "shopify_json"),
                    title=raw.get("title"),
                    price=raw.get("price"),
                    product_media=json.dumps(raw.get("product_media") or {}, ensure_ascii=False),
                    reviews_count=raw.get("reviews_count") or 0,
                    variants=json.dumps(raw.get("variants") or [], ensure_ascii=False),
                    description=raw.get("description"),
                    product_url=raw.get("product_url"),
                    fb_ad_count=self.match_ad_count(raw, ads),
                    matched_ad=json.dumps({}, ensure_ascii=False),
                    collected_at=datetime.utcnow(),
                )
                db.session.add(product)
                saved += 1
            for ad in ads:
                if int(ad.get("ad_count") or 0) >= task.fb_ad_threshold:
                    db.session.add(
                        CompetitorProduct(
                            task_id=task.id,
                            source_domain=domain,
                            source_type="fb_ads",
                            title=ad.get("title"),
                            product_url=ad.get("url"),
                            fb_ad_count=ad.get("ad_count"),
                            matched_ad=json.dumps(ad, ensure_ascii=False),
                            collected_at=datetime.utcnow(),
                        )
                    )
                    saved += 1
        db.session.commit()
        return saved

    def fetch_shopify_products(self, domain, limit):
        url = f"https://{domain}/products.json?limit={max(1, int(limit or 20))}"
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return self.fetch_dom_products(domain, limit)

        products = []
        for item in payload.get("products", []):
            products.append(self.normalize_shopify_product(domain, item))
        return products

    def fetch_dom_products(self, domain, limit):
        try:
            self.fetch(f"https://{domain}", stealthy_headers=True)
        except Exception:
            return []
        return []

    def normalize_shopify_product(self, domain, item):
        variants = item.get("variants") or []
        images = item.get("images") or []
        first_variant = variants[0] if variants else {}
        handle = item.get("handle") or ""
        image_urls = [image.get("src") for image in images if image.get("src")]
        return {
            "source_type": "shopify_json",
            "title": item.get("title"),
            "price": first_variant.get("price"),
            "product_media": {
                "main": image_urls[0] if image_urls else "",
                "carousel": image_urls,
            },
            "variants": [
                {
                    "title": variant.get("title"),
                    "price": variant.get("price"),
                    "available": variant.get("available"),
                }
                for variant in variants
            ],
            "description": strip_html(item.get("body_html") or ""),
            "product_url": f"https://{domain}/products/{handle}" if handle else f"https://{domain}",
            "reviews_count": 0,
        }

    def match_ad_count(self, raw, ads):
        title = (raw.get("title") or "").lower()
        for ad in ads:
            if title and title in (ad.get("title") or "").lower():
                return ad.get("ad_count")
        return None


def strip_html(value):
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(text)).strip()
