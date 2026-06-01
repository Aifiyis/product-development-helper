import json
from pathlib import Path

from flask import current_app


def competitors_path():
    return Path(current_app.root_path) / "data" / "competitors.json"


def load_competitors():
    with competitors_path().open("r", encoding="utf-8") as file:
        return json.load(file)


def save_competitors(data):
    with competitors_path().open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def add_discovered(domain, category, description, scrape_reason):
    data = load_competitors()
    normalized = normalize_domain(domain)
    if not normalized:
        return False
    existing = {item["domain"].lower() for item in data.get("competitors", [])}
    if normalized.lower() in existing:
        return False
    data["competitors"].append(
        {
            "domain": normalized,
            "type": "vertical" if category and category != "comprehensive" else "comprehensive",
            "category": category or "comprehensive",
            "platform": "shopify",
            "description": description or "趋势发现新增竞品",
            "scrape_reason": scrape_reason or "由趋势跟踪候选生成",
            "source": "discovered",
            "selected": False,
        }
    )
    save_competitors(data)
    return True


def list_by_type():
    data = load_competitors()
    grouped = {}
    for item in data.get("competitors", []):
        grouped.setdefault(item.get("category", "comprehensive"), []).append(item)
    return data.get("categories", {}), grouped


def normalize_domain(domain):
    return (domain or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
