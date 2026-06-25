import json
import os
import urllib.request


PAID_TOOL_KEYS = [
    "SEMRUSH_API_KEY",
    "AHREFS_API_KEY",
    "FUNNEL_API_KEY",
    "SUPERMETRICS_API_KEY",
    "META_BUSINESS_TOKEN",
]


def discover_competitors(keyword, category, config):
    for key in PAID_TOOL_KEYS:
        if os.environ.get(key):
            return []
    api_key = config.get("GEMINI_API_KEY")
    if api_key:
        try:
            return discover_with_gemini(keyword, category, api_key, config.get("GEMINI_MODEL"))
        except Exception:
            return fallback_candidates(keyword, category)
    return fallback_candidates(keyword, category)


def discover_with_gemini(keyword, category, api_key, model):
    prompt = (
        "Return strict JSON array only. Generate 5 likely DTC competitor domains for "
        f"keyword={keyword}, category={category}. Each item must include domain, platform, description, scrape_reason. "
        "Platform must be one of shopify, shopline, shoplazza, custom, unknown. Do not include protocol."
    )
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model or 'gemini-2.5-flash'}"
        f":generateContent?key={api_key}"
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.loads(response.read().decode("utf-8"))
    text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return normalize_candidates(json.loads(text), keyword, category)


def fallback_candidates(keyword, category):
    slug = "".join(ch for ch in (keyword or "custom gift").lower() if ch.isalnum())[:18] or "customgift"
    return normalize_candidates(
        [
            {
                "domain": f"{slug}studio.com",
                "platform": "unknown",
                "description": f"{keyword or '定制礼品'}相关趋势候选站",
                "scrape_reason": "本地兜底生成，用于补齐跟踪趋势流程验证",
            }
        ],
        keyword,
        category,
    )


def normalize_candidates(items, keyword, category):
    normalized = []
    for item in items[:10]:
        domain = (item.get("domain") or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        if not domain:
            continue
        normalized.append(
            {
                "domain": domain,
                "platform": item.get("platform") if item.get("platform") in {"shopify", "shopline", "shoplazza", "custom", "unknown"} else "unknown",
                "description": item.get("description") or f"{keyword} 相关竞品",
                "scrape_reason": item.get("scrape_reason") or f"由 {category} 趋势跟踪生成",
            }
        )
    return normalized
