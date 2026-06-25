import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from html import unescape
from urllib.parse import parse_qs, urljoin, urlparse

from app.extensions import db
from app.models import CompetitorProduct
from app.services.competitor_service import load_competitors
from app.services.fb_ads_service import collect_fb_ads
from app.services.scraper_base import ScraperBase


PLATFORM_LABELS = {
    "shopify": "Shopify",
    "shopline": "Shopline",
    "shoplazza": "Shoplazza",
    "custom": "自建站",
    "unknown": "未知",
}


class CompetitorScraper(ScraperBase):
    def __init__(self):
        super().__init__()
        self.errors = []
        self._platform_map = None
        self._playwright = None
        self._browser = None

    def run_collection(self, task):
        saved = 0
        self.errors = []
        try:
            for domain in task.site_list:
                domain_saved = 0
                platform = self.platform_for_domain(domain)
                try:
                    products = self.fetch_products(domain, task.products_per_site, task.sort_mode, platform)
                    fetched_count = len(products)
                    products = self.filter_by_keywords(products, task.keyword_list)
                    if fetched_count and not products and task.keyword_list:
                        self.errors.append(f"{domain}: 获取到 {fetched_count} 个产品，但全部被产品关键词过滤。")
                    ads = collect_fb_ads(domain, task.fb_ad_threshold)
                    for raw in products:
                        raw = self.enrich_product_detail(raw)
                        product = CompetitorProduct(
                            task_id=task.id,
                            source_domain=domain,
                            source_type=raw.get("source_type", f"{platform}_dom"),
                            title=raw.get("title"),
                            price=raw.get("price"),
                            product_created_at=parse_datetime(raw.get("product_created_at")),
                            product_tags=json.dumps(normalize_tags(raw.get("product_tags")), ensure_ascii=False),
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
                        domain_saved += 1
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
                            domain_saved += 1
                    if domain_saved == 0:
                        self.errors.append(f"{domain}: 未保存产品，可能是产品源不可访问、无匹配关键词或页面解析未识别产品列表。")
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback()
                    self.errors.append(f"{domain}: 采集异常（{type(exc).__name__}: {exc}）。")
                    continue
        finally:
            self.close_dynamic_browser()
        return saved

    def platform_for_domain(self, domain):
        if self._platform_map is None:
            try:
                data = load_competitors()
                self._platform_map = {
                    normalize_domain(item.get("domain")): (item.get("platform") or "unknown").lower()
                    for item in data.get("competitors", [])
                }
            except Exception:
                self._platform_map = {}
        return self._platform_map.get(normalize_domain(domain), "unknown")

    def fetch_products(self, domain, limit, sort_mode, platform):
        if platform == "shopify":
            return self.fetch_shopify_products(domain, limit, sort_mode)
        if platform in {"shopline", "shoplazza", "custom"}:
            return self.fetch_dom_products(domain, limit, sort_mode, platform)

        html = self.fetch_page_html(f"https://{domain}/")
        detected = detect_platform(html)
        if detected in {"shopline", "shoplazza", "custom"}:
            self.errors.append(f"{domain}: 自动识别为 {PLATFORM_LABELS.get(detected, detected)}，使用页面解析。")
            return self.fetch_dom_products(domain, limit, sort_mode, detected)
        return self.fetch_shopify_products(domain, limit, sort_mode)

    def filter_by_keywords(self, products, keywords):
        if not keywords:
            return products
        filtered = []
        for product in products:
            haystack = " ".join(
                [
                    product.get("title") or "",
                    product.get("description") or "",
                    product.get("product_url") or "",
                ]
            ).lower()
            if any(keyword in haystack for keyword in keywords):
                filtered.append(product)
        return filtered

    def fetch_shopify_products(self, domain, limit, sort_mode="best_selling"):
        sort_by = "created-descending" if sort_mode == "newest" else "best-selling"
        url = f"https://{domain}/products.json?limit={max(1, int(limit or 20))}&sort_by={sort_by}"
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            self.errors.append(f"{domain}: products.json 访问失败（{type(exc).__name__}: {exc}），已尝试页面兜底解析。")
            return self.fetch_dom_products(domain, limit, sort_mode, "shopify")

        products = []
        for item in payload.get("products", []):
            products.append(self.normalize_shopify_product(domain, item))
        return products

    def fetch_dom_products(self, domain, limit, sort_mode="best_selling", platform="custom"):
        sort_by = "created-descending" if sort_mode == "newest" else "best-selling"
        candidates = collection_urls(domain, sort_by, platform)
        products = []
        seen_urls = set()
        for url in candidates:
            html = self.fetch_page_html(url)
            if not html:
                continue
            detected = detect_platform(html)
            if detected != "unknown" and platform == "custom":
                platform = detected
            for product in parse_product_links(domain, html, platform):
                if product["product_url"] in seen_urls:
                    continue
                seen_urls.add(product["product_url"])
                products.append(product)
                if len(products) >= max(1, int(limit or 20)):
                    return products
        if not products:
            self.errors.append(f"{domain}: 页面可访问，但当前解析器没有识别到产品列表。")
        return products

    def fetch_page_html(self, url):
        try:
            page = self.fetch(url, stealthy_headers=True)
        except Exception as exc:
            parsed = urlparse(url)
            self.errors.append(f"{parsed.netloc or url}: 页面解析失败（{type(exc).__name__}: {exc}）。")
            return ""
        return response_html(page)

    def normalize_shopify_product(self, domain, item):
        variants = item.get("variants") or []
        images = item.get("images") or []
        images_by_id = {image.get("id"): image.get("src") for image in images if image.get("id") and image.get("src")}
        first_variant = variants[0] if variants else {}
        handle = item.get("handle") or ""
        image_urls = unique_urls([image.get("src") for image in images if image.get("src")])
        variant_images = unique_urls(
            [
                variant_image_url(variant, images_by_id)
                for variant in variants
            ]
        )
        image_urls = unique_urls(image_urls + variant_images)
        return {
            "source_type": "shopify_json",
            "title": item.get("title"),
            "price": first_variant.get("price"),
            "product_created_at": item.get("created_at") or item.get("published_at"),
            "product_tags": normalize_tags(item.get("tags")),
            "product_media": {
                "main": image_urls[0] if image_urls else "",
                "carousel": image_urls,
            },
            "variants": [
                {
                    "title": variant.get("title"),
                    "price": variant.get("price"),
                    "available": variant.get("available"),
                    "image": variant_image_url(variant, images_by_id),
                }
                for variant in variants
            ],
            "description": item.get("body_html") or "",
            "product_url": f"https://{domain}/products/{handle}" if handle else f"https://{domain}",
            "reviews_count": 0,
        }

    def enrich_product_detail(self, raw):
        product_url = raw.get("product_url")
        if not product_url:
            return raw
        html = self.fetch_dynamic_html(product_url)
        if not html:
            html = self.fetch_page_html(product_url)
        if not html:
            return raw

        detail = parse_product_detail(product_url, html)
        if detail.get("title"):
            raw["title"] = prefer_detail_title(raw.get("title"), detail["title"])
        if detail.get("price"):
            raw["price"] = detail["price"]
        if detail.get("description"):
            raw["description"] = raw.get("description") or detail["description"]
        if detail.get("reviews_count"):
            raw["reviews_count"] = detail["reviews_count"]
        if detail.get("product_created_at"):
            raw["product_created_at"] = raw.get("product_created_at") or detail["product_created_at"]
        if detail.get("product_tags"):
            raw["product_tags"] = normalize_tags(raw.get("product_tags")) or detail["product_tags"]

        current_media = raw.get("product_media") or {}
        merged_images = unique_urls(
            [current_media.get("main")]
            + (current_media.get("carousel") or [])
            + [variant.get("image") for variant in (raw.get("variants") or []) if isinstance(variant, dict)]
            + (detail.get("images") or [])
            + [variant.get("image") for variant in (detail.get("variants") or []) if isinstance(variant, dict)]
        )
        if merged_images:
            raw["product_media"] = {"main": merged_images[0], "carousel": merged_images}

        variants = (raw.get("variants") or []) + (detail.get("variants") or [])
        raw["variants"] = dedupe_variants(variants)
        return raw

    def fetch_dynamic_html(self, product_url):
        page = None
        try:
            page = self.dynamic_page()
            page.goto(product_url, wait_until="load", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            page.wait_for_timeout(3500)
            selectors = [
                ".jdgm-widget",
                ".jdgm-prev-badge",
                "[data-customily]",
                "[class*='customily']",
                "[class*='teeinblue']",
                "[data-teeinblue]",
                "[class*='ymq']",
                "[id*='ymq']",
                "form[action*='/cart/add']",
            ]
            for selector in selectors:
                try:
                    page.wait_for_selector(selector, timeout=2500)
                    break
                except Exception:
                    continue
            return page.content()
        except Exception as exc:
            self.errors.append(f"{product_url}: 动态详情页解析失败（{type(exc).__name__}: {exc}），已尝试静态详情页。")
            return ""
        finally:
            if page is not None:
                try:
                    page.context.close()
                except Exception:
                    pass

    def dynamic_page(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            last_error = None
            for channel in ("chrome", "msedge"):
                try:
                    self._browser = self._playwright.chromium.launch(channel=channel, headless=True)
                    break
                except Exception as exc:
                    last_error = exc
            if self._browser is None:
                raise RuntimeError(f"本机 Chrome/Edge 启动失败：{last_error}")
        context = self._browser.new_context(
            viewport={"width": 1440, "height": 1200},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.set_default_timeout(20000)
        return page

    def close_dynamic_browser(self):
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def match_ad_count(self, raw, ads):
        title = (raw.get("title") or "").lower()
        for ad in ads:
            if title and title in (ad.get("title") or "").lower():
                return ad.get("ad_count")
        return None


def collection_urls(domain, sort_by, platform):
    if platform == "shopline":
        return [
            f"https://{domain}/collections/all?sort_by={sort_by}",
            f"https://{domain}/collections/all",
            f"https://{domain}/collections",
            f"https://{domain}",
        ]
    if platform == "shoplazza":
        return [
            f"https://{domain}/collections/all?sort_by={sort_by}",
            f"https://{domain}/collections/all",
            f"https://{domain}/collections",
            f"https://{domain}",
        ]
    return [
        f"https://{domain}/collections/all?sort_by={sort_by}",
        f"https://{domain}/collections/all",
        f"https://{domain}/collections",
        f"https://{domain}",
    ]


def detect_platform(html):
    lower = (html or "").lower()
    if "myshopline" in lower or "shopline" in lower:
        return "shopline"
    if "shoplazza" in lower or "shoplazza.com" in lower:
        return "shoplazza"
    if "cdn.shopify" in lower or "shopify.theme" in lower or "window.shopify" in lower or "__st" in lower:
        return "shopify"
    return "unknown"


def response_html(page):
    if page is None:
        return ""
    body = getattr(page, "body", None)
    if isinstance(body, bytes):
        return body.decode(getattr(page, "encoding", None) or "utf-8", errors="replace")
    if isinstance(body, str):
        return body
    html_content = getattr(page, "html_content", None)
    if isinstance(html_content, bytes):
        return html_content.decode(getattr(page, "encoding", None) or "utf-8", errors="replace")
    if isinstance(html_content, str):
        return html_content
    return str(page)


def parse_product_links(domain, html, platform="custom"):
    if not html:
        return []
    products = []
    base_url = f"https://{domain}"
    pattern = r'(?is)<a\b[^>]*href=["\']([^"\']*/products/[^"\']+)["\'][^>]*>(.*?)</a>'
    for href, inner in re.findall(pattern, html):
        product_url = normalize_product_url(base_url, href)
        if not product_url:
            continue
        title = strip_html(inner) or title_from_product_url(product_url)
        images = extract_images_near_link(html, href, base_url)
        products.append(
            {
                "source_type": f"{platform}_dom_product_link",
                "title": title,
                "price": extract_price_near_link(html, href),
                "product_created_at": None,
                "product_tags": [],
                "product_media": {
                    "main": images[0] if images else "",
                    "carousel": images,
                },
                "variants": [],
                "description": "",
                "product_url": product_url,
                "reviews_count": 0,
            }
        )
    return products


def parse_product_detail(product_url, html):
    title = first_match(
        html,
        [
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            r"<h1[^>]*>(.*?)</h1>",
        ],
    )
    description = extract_description_html(html)
    images = extract_product_images(product_url, html)
    variants = parse_standard_variants(html) + parse_plugin_variants(html)
    return {
        "title": strip_html(unescape(title)) if title else "",
        "price": extract_price_near_product(html),
        "description": description,
        "images": images,
        "product_created_at": extract_product_created_at(html),
        "product_tags": extract_product_tags(html),
        "reviews_count": extract_reviews_count(html),
        "variants": variants,
    }


def extract_product_images(product_url, html):
    base_url = product_url
    images = []
    variant_images = []

    for pattern in [
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
    ]:
        images.extend(re.findall(pattern, html, flags=re.I))

    for script in re.findall(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html):
        try:
            payload = json.loads(unescape(script.strip()))
        except Exception:
            continue
        images.extend(images_from_json_ld(payload))
        variant_images.extend(variant_images_from_json(payload))

    gallery_chunks = re.findall(
        r'(?is)<(?:div|section|ul|ol)[^>]+(?:class|id)=["\'][^"\']*(?:product[-_ ]?media|product[-_ ]?gallery|product[-_ ]?slider|product[-_ ]?carousel|main[-_ ]?media|media[-_ ]?gallery)[^"\']*["\'][^>]*>.*?</(?:div|section|ul|ol)>',
        html,
    )
    for chunk in gallery_chunks[:40]:
        if is_review_or_thumbnail_chunk(chunk):
            continue
        images.extend(extract_img_sources(chunk))

    script_images = product_media_from_scripts(html)
    images.extend(script_images.get("media", []))
    variant_images.extend(script_images.get("variant_images", []))

    normalized = [normalize_image_url(base_url, image) for image in images + variant_images]
    return unique_urls([image for image in normalized if is_product_image(image)])


def images_from_json_ld(payload):
    images = []
    if isinstance(payload, list):
        for item in payload:
            images.extend(images_from_json_ld(item))
    elif isinstance(payload, dict):
        payload_type = payload.get("@type")
        is_product = payload_type == "Product" or (isinstance(payload_type, list) and "Product" in payload_type)
        if is_product:
            image = payload.get("image")
            if isinstance(image, list):
                images.extend(image)
            elif isinstance(image, str):
                images.append(image)
        for value in payload.values():
            if isinstance(value, (dict, list)):
                images.extend(images_from_json_ld(value))
    return images


def variant_images_from_json(payload):
    images = []
    if isinstance(payload, list):
        for item in payload:
            images.extend(variant_images_from_json(item))
    elif isinstance(payload, dict):
        if any(key in payload for key in ("variants", "variant", "options")):
            images.extend(images_from_variant_payload(payload))
        for key, value in payload.items():
            if key in {"variants", "variant", "options"} or looks_like_variant_key(key):
                images.extend(images_from_variant_payload(value))
            elif isinstance(value, (dict, list)):
                images.extend(variant_images_from_json(value))
    return images


def images_from_variant_payload(payload):
    images = []
    if isinstance(payload, list):
        for item in payload:
            images.extend(images_from_variant_payload(item))
    elif isinstance(payload, dict):
        for key in ("image", "featured_image", "variant_image", "img", "src"):
            value = payload.get(key)
            if isinstance(value, str):
                images.append(value)
            elif isinstance(value, dict):
                for nested_key in ("src", "url", "origin_src"):
                    if nested_key in value:
                        images.append(value[nested_key])
        for value in payload.values():
            if isinstance(value, (dict, list)):
                images.extend(images_from_variant_payload(value))
    return images


def product_media_from_scripts(html):
    media = []
    variant_images = []
    script_bodies = re.findall(r"(?is)<script[^>]*>(.*?)</script>", html)
    for script in script_bodies:
        lower = script.lower()
        if any(token in lower for token in ("judgeme", "judge.me", "review", "reviews", "testimonial", "customer_photo")):
            continue
        if not any(token in lower for token in ("product", "media", "variant", "variants", "images")):
            continue
        parsed = parse_script_json(script)
        if parsed is not None:
            media.extend(images_from_product_payload(parsed))
            variant_images.extend(variant_images_from_json(parsed))
            continue
        urls = re.findall(r'["\']((?:https?:)?//[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?)["\']', script, flags=re.I)
        if "variant" in lower or "variants" in lower:
            variant_images.extend(urls)
        if "media" in lower or "product" in lower:
            media.extend(urls)
    return {"media": media, "variant_images": variant_images}


def images_from_product_payload(payload):
    images = []
    if isinstance(payload, list):
        for item in payload:
            images.extend(images_from_product_payload(item))
    elif isinstance(payload, dict):
        for key in ("media", "images", "product_media"):
            if key in payload:
                images.extend(images_from_media_payload(payload[key]))
        if payload.get("@type") == "Product":
            images.extend(images_from_media_payload(payload.get("image")))
        for value in payload.values():
            if isinstance(value, (dict, list)):
                images.extend(images_from_product_payload(value))
    return images


def images_from_media_payload(payload):
    images = []
    if isinstance(payload, str):
        images.append(payload)
    elif isinstance(payload, list):
        for item in payload:
            images.extend(images_from_media_payload(item))
    elif isinstance(payload, dict):
        for key in ("src", "url", "origin_src", "preview_image", "image"):
            value = payload.get(key)
            if isinstance(value, str):
                images.append(value)
            elif isinstance(value, dict):
                images.extend(images_from_media_payload(value))
        for value in payload.values():
            if isinstance(value, (dict, list)):
                images.extend(images_from_media_payload(value))
    return images


def parse_script_json(script):
    text = unescape((script or "").strip())
    if not text:
        return None
    candidates = [text]
    assignment = re.search(r"=\s*({.*})\s*;?\s*$", text, flags=re.S)
    if assignment:
        candidates.append(assignment.group(1))
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


def looks_like_variant_key(key):
    return isinstance(key, str) and "variant" in key.lower()


def extract_img_sources(html):
    sources = []
    for tag in re.findall(r"(?is)<img\b[^>]*>", html):
        if is_review_or_thumbnail_chunk(tag):
            continue
        if image_tag_width(tag) is not None and image_tag_width(tag) <= 600:
            continue
        for attr in ("src", "data-src", "data-original", "data-image", "data-zoom-image"):
            match = re.search(attr + r'=["\']([^"\']+)["\']', tag, flags=re.I)
            if match:
                sources.append(match.group(1))
        srcset = re.search(r'(?:srcset|data-srcset)=["\']([^"\']+)["\']', tag, flags=re.I)
        if srcset:
            sources.extend(src for src, width in parse_srcset(srcset.group(1)) if width is None or width > 600)
    for url in re.findall(r'["\']((?:https?:)?//[^"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^"\']*)?)["\']', html, flags=re.I):
        sources.append(url)
    return sources


def extract_images_near_link(html, href, base_url):
    position = html.find(href)
    if position < 0:
        return []
    chunk = html[max(0, position - 2500) : position + 2500]
    return unique_urls(
        [
            image
            for image in (normalize_image_url(base_url, src) for src in extract_img_sources(chunk))
            if is_product_image(image)
        ]
    )


def normalize_image_url(base_url, src):
    if not src:
        return ""
    src = unescape(src).strip()
    if src.startswith("//"):
        src = "https:" + src
    return canonical_image_url(urljoin(base_url, src))


def parse_srcset(srcset):
    candidates = []
    for item in (srcset or "").split(","):
        parts = item.strip().split()
        if not parts:
            continue
        width = None
        if len(parts) > 1 and parts[1].endswith("w"):
            try:
                width = int(parts[1][:-1])
            except ValueError:
                width = None
        candidates.append((parts[0], width))
    return candidates


def image_tag_width(tag):
    for attr in ("width", "data-width"):
        match = re.search(attr + r'=["\']?(\d+)["\']?', tag, flags=re.I)
        if match:
            return int(match.group(1))
    style = re.search(r'width\s*:\s*(\d+)px', tag, flags=re.I)
    return int(style.group(1)) if style else None


def image_url_width(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    for key in ("width", "w"):
        if key in params and params[key]:
            try:
                return int(params[key][0])
            except ValueError:
                return None
    match = re.search(r"[_-](\d{2,4})x(?:[_\-.]|$)", parsed.path)
    return int(match.group(1)) if match else None


def is_review_or_thumbnail_chunk(value):
    lower = (value or "").lower()
    review_tokens = (
        "judgeme",
        "judge.me",
        "jdgm",
        "review",
        "reviews",
        "testimonial",
        "customer-photo",
        "customer_photo",
        "ugc",
        "rating",
    )
    thumbnail_tokens = (
        "thumbnail",
        "thumb",
        "swiper-thumb",
        "product-thumb",
        "media-thumb",
        "gallery-thumb",
    )
    return any(token in lower for token in review_tokens + thumbnail_tokens)


def is_review_image_url(url):
    lower = (url or "").lower()
    return any(
        token in lower
        for token in (
            "judgeme",
            "judge.me",
            "jdgm",
            "review",
            "reviews",
            "testimonial",
            "customer-photo",
            "customer_photo",
        )
    )


def canonical_image_url(url):
    parsed = urlparse(url)
    if re.search(r"\.(jpg|jpeg|png|webp)$", parsed.path, flags=re.I):
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    match = re.match(r"^(.*?\.(?:jpg|jpeg|png|webp))", url, flags=re.I)
    return match.group(1) if match else url


def image_filename(url):
    return urlparse(url).path.rsplit("/", 1)[-1]


def sized_image_variant(url):
    filename = image_filename(url)
    return bool(re.search(r"_(?:\d{3,5})x(?=\.(?:jpg|jpeg|png|webp)$)", filename, flags=re.I))


def image_dedupe_key(url):
    filename = image_filename(url)
    filename = re.sub(r"_(?:\d{3,5})x(?=\.(?:jpg|jpeg|png|webp)$)", "", filename, flags=re.I)
    match = re.match(r"(.+\.(?:jpg|jpeg|png|webp))$", filename, flags=re.I)
    return match.group(1).lower() if match else url.lower()


def is_product_image(url):
    if not url:
        return False
    lower = url.lower()
    if not re.search(r"\.(jpg|jpeg|png|webp)(?:\?|$)", lower):
        return False
    if is_review_image_url(lower):
        return False
    width = image_url_width(lower)
    if width is not None and width <= 600:
        return False
    blocked = ["logo", "icon", "favicon", "sprite", "payment", "badge", "avatar", "placeholder", "thumbnail", "thumb"]
    return not any(token in lower for token in blocked)


def parse_standard_variants(html):
    variants = []
    for chunk in re.findall(r'(?is)<select\b[^>]*name=["\'][^"\']*(?:id|variant|option)[^"\']*["\'][^>]*>.*?</select>', html):
        label = extract_label(chunk) or "variant"
        values = extract_values(chunk)
        if values:
            variants.append({"title": label, "price": "", "available": True, "source": "standard_select", "values": values})
    for fieldset in re.findall(r"(?is)<fieldset\b[^>]*>.*?</fieldset>", html):
        label = extract_legend(fieldset) or extract_label(fieldset) or "option"
        values = extract_values(fieldset)
        if values:
            variants.append({"title": label, "price": "", "available": True, "source": "standard_fieldset", "values": values})
    return variants[:80]


def parse_plugin_variants(html):
    if not html:
        return []
    variants = []
    plugin_patterns = [
        ("customily", r"(?is)<(?:select|input|textarea|div|span|label|fieldset)[^>]*(?:customily)[^>]*>.*?</(?:select|textarea|div|span|label|fieldset)>"),
        ("teeinblue", r"(?is)<(?:select|input|textarea|div|span|label|fieldset)[^>]*(?:teeinblue)[^>]*>.*?</(?:select|textarea|div|span|label|fieldset)>"),
        ("YMQ", r"(?is)<(?:select|input|textarea|div|span|label|fieldset)[^>]*(?:ymq)[^>]*>.*?</(?:select|textarea|div|span|label|fieldset)>"),
    ]
    for source, pattern in plugin_patterns:
        for chunk in re.findall(pattern, html):
            label = extract_label(chunk) or extract_legend(chunk) or source
            values = extract_values(chunk)
            if label or values:
                variants.append(
                    {
                        "title": label,
                        "price": "",
                        "available": True,
                        "source": f"{source}_dom",
                        "values": values,
                    }
                )
    return variants[:120]


def extract_reviews_count(html):
    candidates = []
    for pattern in [
        r'data-number-of-reviews=["\'](\d+)["\']',
        r'data-review-count=["\'](\d+)["\']',
        r'"reviewCount"\s*:\s*"?(\d+)"?',
        r'"ratingCount"\s*:\s*"?(\d+)"?',
        r'(\d[\d,\.]*)\s*(?:reviews?|review\(s\)|customer reviews?|评价)',
    ]:
        for match in re.findall(pattern, html, flags=re.I):
            candidates.append(to_int(match))
    return max([value for value in candidates if value is not None], default=0)


def extract_product_created_at(html):
    for script in re.findall(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html):
        try:
            payload = json.loads(unescape(script.strip()))
        except Exception:
            continue
        value = first_product_date(payload)
        if value:
            return value
    for pattern in [
        r'<meta[^>]+property=["\'](?:product:published_time|article:published_time)["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\'](?:product:published_time|article:published_time)["\']',
        r'"(?:created_at|createdAt|published_at|publishedAt|dateCreated|datePublished)"\s*:\s*"([^"]+)"',
    ]:
        match = re.search(pattern, html, flags=re.I)
        if match:
            return match.group(1)
    return None


def first_product_date(payload):
    if isinstance(payload, list):
        for item in payload:
            value = first_product_date(item)
            if value:
                return value
    if isinstance(payload, dict):
        payload_type = payload.get("@type")
        is_product = payload_type == "Product" or (isinstance(payload_type, list) and "Product" in payload_type)
        if is_product:
            for key in ("dateCreated", "datePublished", "releaseDate"):
                if payload.get(key):
                    return payload[key]
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found = first_product_date(value)
                if found:
                    return found
    return None


def extract_product_tags(html):
    tags = []
    for script in re.findall(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html):
        try:
            payload = json.loads(unescape(script.strip()))
        except Exception:
            continue
        tags.extend(tags_from_json(payload))
    for pattern in [
        r'<meta[^>]+name=["\']keywords["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']keywords["\']',
        r'"tags"\s*:\s*(\[[^\]]+\]|"[^"]+")',
    ]:
        for match in re.findall(pattern, html, flags=re.I):
            tags.extend(normalize_tags(match))
    return normalize_tags(tags)


def tags_from_json(payload):
    tags = []
    if isinstance(payload, list):
        for item in payload:
            tags.extend(tags_from_json(item))
    elif isinstance(payload, dict):
        payload_type = payload.get("@type")
        is_product = payload_type == "Product" or (isinstance(payload_type, list) and "Product" in payload_type)
        if is_product:
            for key in ("keywords", "category", "brand"):
                tags.extend(normalize_tags(payload.get(key)))
        for value in payload.values():
            if isinstance(value, (dict, list)):
                tags.extend(tags_from_json(value))
    return tags


def extract_description_html(html):
    patterns = [
        r'(?is)<(?:div|section)[^>]+(?:class|id)=["\'][^"\']*(?:product__description|product-description|product_detail|description|rte)[^"\']*["\'][^>]*>.*?</(?:div|section)>',
        r'(?is)<div[^>]+itemprop=["\']description["\'][^>]*>.*?</div>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return clean_html_fragment(match.group(0))
    return ""


def clean_html_fragment(html):
    html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "", html)
    return html.strip()


def strip_html(value):
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def first_match(html, patterns):
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.I | re.S)
        if match:
            return match.group(1)
    return ""


def extract_label(html):
    for pattern in [
        r'aria-label=["\']([^"\']+)["\']',
        r'name=["\']([^"\']+)["\']',
        r'data-name=["\']([^"\']+)["\']',
    ]:
        match = re.search(pattern, html, flags=re.I)
        if match:
            return strip_html(unescape(match.group(1)))
    text = strip_html(html)
    return text[:80]


def extract_legend(html):
    match = re.search(r"(?is)<legend[^>]*>(.*?)</legend>", html)
    return strip_html(match.group(1)) if match else ""


def extract_values(html):
    values = []
    for option in re.findall(r"(?is)<option[^>]*>(.*?)</option>", html):
        value = strip_html(option)
        if value:
            values.append(value)
    for value in re.findall(r'value=["\']([^"\']+)["\']', html, flags=re.I):
        clean = strip_html(unescape(value))
        if clean and clean not in values and not re.match(r"^\d{8,}$", clean):
            values.append(clean)
    for label in re.findall(r"(?is)<label[^>]*>(.*?)</label>", html):
        clean = strip_html(label)
        if clean and clean not in values:
            values.append(clean)
    return values[:40]


def extract_price_near_link(html, href):
    position = html.find(href)
    if position < 0:
        return None
    return extract_price_near_product(html[position : position + 2500])


def extract_price_near_product(html):
    structured = extract_structured_price(html)
    if structured:
        return structured
    match = re.search(r"[$€£]\s?\d+(?:[.,]\d{1,2})?", html)
    return match.group(0) if match else None


def extract_structured_price(html):
    amount = first_match(
        html,
        [
            r'<meta[^>]+property=["\']og:price:amount["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:price:amount["\']',
            r'<meta[^>]+(?:itemprop|property)=["\']price["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:itemprop|property)=["\']price["\']',
            r'Price\s*:\s*["\']([$€£]\s?\d+(?:[.,]\d{1,2})?)["\']',
        ],
    )
    if amount:
        if re.match(r"[$€£]", amount.strip()):
            return amount.strip()
        currency = first_match(
            html,
            [
                r'<meta[^>]+property=["\']og:price:currency["\'][^>]+content=["\']([^"\']+)["\']',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:price:currency["\']',
            ],
        )
        return format_price(amount, currency)

    for script in re.findall(r'(?is)<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html):
        try:
            payload = json.loads(unescape(script.strip()))
        except Exception:
            continue
        price = price_from_json_ld(payload)
        if price:
            return price
    return None


def price_from_json_ld(payload):
    if isinstance(payload, list):
        for item in payload:
            price = price_from_json_ld(item)
            if price:
                return price
    if isinstance(payload, dict):
        offers = payload.get("offers")
        if offers:
            price = price_from_offer(offers)
            if price:
                return price
        for value in payload.values():
            if isinstance(value, (dict, list)):
                price = price_from_json_ld(value)
                if price:
                    return price
    return None


def price_from_offer(offer):
    if isinstance(offer, list):
        for item in offer:
            price = price_from_offer(item)
            if price:
                return price
    if isinstance(offer, dict):
        amount = offer.get("price") or offer.get("lowPrice")
        if amount:
            return format_price(amount, offer.get("priceCurrency"))
    return None


def format_price(amount, currency=None):
    text = str(amount).strip()
    if not text:
        return None
    symbol = {"USD": "$", "EUR": "€", "GBP": "£"}.get((currency or "USD").upper(), "")
    return f"{symbol}{text}" if symbol and not text.startswith(symbol) else text


def normalize_product_url(base_url, href):
    url = urljoin(base_url, unescape(href))
    parsed = urlparse(url)
    if "/products/" not in parsed.path:
        return ""
    clean_path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{clean_path}"


def normalize_domain(domain):
    return (domain or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")


def parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        for pattern, length in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
            try:
                return datetime.strptime(text[:length], pattern)
            except ValueError:
                continue
    return None


def normalize_tags(value):
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            try:
                return normalize_tags(json.loads(stripped))
            except Exception:
                pass
        raw_tags = re.split(r"[,|;]", stripped)
    elif isinstance(value, dict):
        raw_tags = [value.get("name") or value.get("title") or value.get("value")]
    else:
        raw_tags = value
    result = []
    seen = set()
    for item in raw_tags:
        if isinstance(item, dict):
            item = item.get("name") or item.get("title") or item.get("value")
        tag = strip_html(str(item or "")).strip()
        if not tag:
            continue
        key = tag.lower()
        if key not in seen:
            seen.add(key)
            result.append(tag)
    return result[:80]


def variant_image_url(variant, images_by_id):
    featured = variant.get("featured_image")
    if isinstance(featured, dict):
        for key in ("src", "url"):
            if featured.get(key):
                return featured[key]
    if variant.get("image"):
        return variant.get("image")
    image_id = variant.get("image_id") or variant.get("featured_image_id")
    return images_by_id.get(image_id, "")


def title_from_product_url(product_url):
    handle = product_url.rstrip("/").split("/")[-1]
    return handle.replace("-", " ").replace("_", " ").title()


def prefer_detail_title(current, detail):
    current = current or ""
    if not current or len(current) < 12:
        return detail
    if current.lower().startswith(("add to cart", "quick view", "shop now")):
        return detail
    return current


def unique_urls(urls):
    seen = {}
    order = []
    for url in urls:
        if not url:
            continue
        clean = canonical_image_url(str(url).strip())
        key = image_dedupe_key(clean) if is_product_image(clean) else clean
        if not clean:
            continue
        if key not in seen:
            seen[key] = clean
            order.append(key)
            continue
        if sized_image_variant(seen[key]) and not sized_image_variant(clean):
            seen[key] = clean
    return [seen[key] for key in order]


def dedupe_variants(variants):
    seen = set()
    result = []
    for variant in variants:
        key = json.dumps(
            {
                "title": variant.get("title"),
                "source": variant.get("source"),
                "values": variant.get("values") or [],
                "price": variant.get("price"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(variant)
    return result[:160]


def to_int(value):
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None
