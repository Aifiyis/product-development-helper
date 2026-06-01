import json
import urllib.error
import urllib.request
from datetime import datetime


PLATFORMS = ["TikTok", "Instagram", "Facebook", "Pinterest"]
LANGUAGES = ["英语", "法语", "日语", "德语", "西班牙语", "意大利语"]
AD_CATEGORIES = ["通用", "电商", "游戏", "美妆", "服装", "礼品", "应用", "家居", "旅行", "食品"]


def discover_trends(platform, language, category, api_key=None, model=None):
    if api_key:
        try:
            return _discover_with_gemini(platform, language, category, api_key, model)
        except Exception as exc:
            result = _fallback_trends(platform, language, category)
            result["source"] = f"sample fallback: {exc.__class__.__name__}"
            return result
    return _fallback_trends(platform, language, category)


def _discover_with_gemini(platform, language, category, api_key, model):
    prompt = (
        "You are a social media trend analyst for performance marketing. "
        f"Generate current rising social trends for platform={platform}, language={language}, ad category={category}. "
        "Return strict JSON only with keys hashtags and topics. "
        "hashtags must contain exactly 10 items. topics must contain exactly 5 items. "
        "Each item must have tag or title, volume, trend, insight. "
        "Use hashtag text in the requested language where appropriate. Keep insights concise and useful for ad optimization."
    )
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model or 'gemini-1.5-flash'}"
        f":generateContent?key={api_key}"
    )
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.removeprefix("json").strip()
    parsed = json.loads(text)
    return _normalize_result(platform, language, category, parsed, source="gemini")


def _fallback_trends(platform, language, category):
    seed = _localized_seed(language)
    hashtags = [
        _hashtag(f"#{seed['trend']}Finds", "↑ Rising", "High intent discovery behavior for paid social creatives.", 0),
        _hashtag(f"#{seed['shop']}Tok", "↑ Rising", "Short-form shopping proof points and product demos are accelerating.", 1),
        _hashtag(f"#{seed['beauty']}Routine", "↑ Rising", "Routine-led content gives hooks for creator-style ad angles.", 2),
        _hashtag(f"#{seed['deal']}Alert", "↑ Rising", "Offer-led posts are gaining share for conversion campaigns.", 3),
        _hashtag(f"#{seed['gift']}Ideas", "↑ Rising", "Gift discovery spikes around seasonal and relationship moments.", 4),
        _hashtag(f"#{seed['game']}Launch", "→ Stable", "Launch previews and reward mechanics remain useful for installs.", 5),
        _hashtag(f"#{seed['style']}Check", "↑ Rising", "Outfit and comparison formats support apparel testing.", 6),
        _hashtag(f"#{seed['home']}Upgrade", "→ Stable", "Before-and-after home content sustains saves and retargeting pools.", 7),
        _hashtag(f"#{seed['must']}Have", "↑ Rising", "Broad product curiosity tag for prospecting audiences.", 8),
        _hashtag(f"#{seed['viral']}Review", "↑ Rising", "Review hooks help translate social proof into ad copy.", 9),
    ]
    topics = [
        _topic("Creator-led product demos", "↑ Rising", "Use creator hooks, fast proof, and one clear product claim."),
        _topic("Comparison shopping", "↑ Rising", "Angle ad tests around alternatives, price, quality, and time saved."),
        _topic("Routine integration", "→ Stable", "Show how the product fits an existing daily workflow."),
        _topic("Seasonal gifting", "↑ Rising", "Package offers by recipient, occasion, and urgency."),
        _topic("Before-and-after proof", "↑ Rising", "Use visible transformation as the first three-second hook."),
    ]
    return _normalize_result(
        platform,
        language,
        category,
        {"hashtags": hashtags, "topics": topics},
        source="sample",
    )


def _normalize_result(platform, language, category, payload, source):
    return {
        "platform": platform,
        "language": language,
        "category": category,
        "source": source,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "hashtags": _limit_items(payload.get("hashtags", []), 10, "tag"),
        "topics": _limit_items(payload.get("topics", []), 5, "title"),
    }


def _limit_items(items, limit, name_key):
    normalized = []
    for item in items[:limit]:
        normalized.append(
            {
                name_key: item.get(name_key) or item.get("tag") or item.get("title") or "",
                "volume": item.get("volume", "N/A"),
                "trend": item.get("trend", "↑ Rising"),
                "insight": item.get("insight", ""),
            }
        )
    return normalized


def _hashtag(tag, trend, insight, index):
    return {"tag": tag, "volume": f"{18 + index * 7}.{index % 9}M", "trend": trend, "insight": insight}


def _topic(title, trend, insight):
    return {"title": title, "volume": "High", "trend": trend, "insight": insight}


def _localized_seed(language):
    seeds = {
        "法语": {"trend": "Tendance", "shop": "Achat", "beauty": "Beaute", "deal": "Promo", "gift": "Cadeau", "game": "Jeu", "style": "Style", "home": "Maison", "must": "Incontournable", "viral": "Avis"},
        "日语": {"trend": "Trend", "shop": "Shop", "beauty": "Beauty", "deal": "Deal", "gift": "Gift", "game": "Game", "style": "Style", "home": "Home", "must": "Must", "viral": "Review"},
        "德语": {"trend": "Trend", "shop": "Kauf", "beauty": "Beauty", "deal": "Angebot", "gift": "Geschenk", "game": "Game", "style": "Style", "home": "Zuhause", "must": "Must", "viral": "Review"},
        "西班牙语": {"trend": "Tendencia", "shop": "Compra", "beauty": "Belleza", "deal": "Oferta", "gift": "Regalo", "game": "Juego", "style": "Estilo", "home": "Hogar", "must": "Must", "viral": "Review"},
        "意大利语": {"trend": "Tendenza", "shop": "Shopping", "beauty": "Bellezza", "deal": "Offerta", "gift": "Regalo", "game": "Gioco", "style": "Stile", "home": "Casa", "must": "Must", "viral": "Review"},
        "French": {"trend": "Tendance", "shop": "Achat", "beauty": "Beaute", "deal": "Promo", "gift": "Cadeau", "game": "Jeu", "style": "Style", "home": "Maison", "must": "Incontournable", "viral": "Avis"},
        "Japanese": {"trend": "Trend", "shop": "Shop", "beauty": "Beauty", "deal": "Deal", "gift": "Gift", "game": "Game", "style": "Style", "home": "Home", "must": "Must", "viral": "Review"},
        "German": {"trend": "Trend", "shop": "Kauf", "beauty": "Beauty", "deal": "Angebot", "gift": "Geschenk", "game": "Game", "style": "Style", "home": "Zuhause", "must": "Must", "viral": "Review"},
        "Spanish": {"trend": "Tendencia", "shop": "Compra", "beauty": "Belleza", "deal": "Oferta", "gift": "Regalo", "game": "Juego", "style": "Estilo", "home": "Hogar", "must": "Must", "viral": "Review"},
        "Italian": {"trend": "Tendenza", "shop": "Shopping", "beauty": "Bellezza", "deal": "Offerta", "gift": "Regalo", "game": "Gioco", "style": "Stile", "home": "Casa", "must": "Must", "viral": "Review"},
    }
    return seeds.get(language, {"trend": "Trend", "shop": "Shop", "beauty": "Beauty", "deal": "Deal", "gift": "Gift", "game": "Game", "style": "Style", "home": "Home", "must": "Must", "viral": "Review"})
