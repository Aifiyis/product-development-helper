import json
import urllib.request


ASPECT_RATIOS = [
    ("1:1", "1:1 (Instagram Post)"),
    ("4:5", "4:5 (Feed Ad)"),
    ("9:16", "9:16 (Story/Reels/TikTok)"),
    ("16:9", "16:9 (YouTube/Facebook)"),
]


THEMES = [
    ("晨间通勤搭子", "放在办公桌和电脑旁，呈现一天开始前的秩序感。", "解决高频通勤场景中的便携与稳定需求。"),
    ("周末露营补给", "摆在露营桌、折叠椅和暖色营灯之间，突出户外氛围。", "把产品延伸到轻户外、家庭周末和礼品场景。"),
    ("健身后恢复时刻", "放在运动包、毛巾和训练鞋旁，画面清爽有能量。", "绑定健康生活方式，强化随身携带和即时补水。"),
    ("亲子出行安心包", "出现在婴儿车、零食袋和车载收纳旁，明亮温暖。", "放大安全、防漏、省心等家庭决策卖点。"),
    ("办公室效率角", "与笔记本、便利贴、键盘和日程本组合，干净专业。", "把功能卖点翻译成效率、专注和长期使用价值。"),
    ("节日礼物开箱", "礼盒、丝带和贺卡围绕产品，光线有节日仪式感。", "适合礼品广告，突出体面、实用和不出错。"),
    ("自驾车内必备", "置于车载杯架、地图和墨镜旁，带自然窗光。", "延伸到驾驶、旅行和长途场景，强化稳定适配。"),
    ("小户型收纳美学", "放在开放式架子或厨房台面，色彩克制高级。", "适合家居类视觉，突出好看、不占地、易收纳。"),
    ("城市咖啡替代", "与咖啡豆、外带杯和街角橱窗形成对比。", "把省钱、环保和自带饮品习惯转化为广告钩子。"),
    ("礼赠企业客户", "摆在会议桌、品牌卡片和简洁包装旁，商务感强。", "拓展 B2B、员工福利和客户礼赠使用场景。"),
]


def generate_product_concepts(form, api_key=None, model=None):
    if api_key:
        try:
            return _generate_with_gemini(form, api_key, model), "gemini"
        except Exception as exc:
            return _fallback_concepts(form), f"sample fallback: {exc.__class__.__name__}"
    return _fallback_concepts(form), "sample"


def _generate_with_gemini(form, api_key, model):
    product = form["product_name"].strip() or "产品"
    audience = form["audience"].strip() or "目标用户"
    selling_point = form["selling_point"].strip() or "核心卖点"
    ad_copy = form["ad_copy"].strip() or "Brand moment"
    aspect_ratio = form["aspect_ratio"]
    prompt = (
        "You are a senior direct-response creative strategist. "
        "Generate exactly 10 product extension ad concepts for a paid social creative tool. "
        "Return strict JSON only: an array of objects with keys theme, scene, angle, selling_point, prompt. "
        f"Product: {product}. Target audience: {audience}. Selling points: {selling_point}. "
        f"Ad copy overlay: {ad_copy}. Aspect ratio: {aspect_ratio}. "
        "Each concept should extend the product into a distinct use case or buying motivation. "
        "Write theme, scene, angle, and selling_point in Chinese. "
        "Write prompt in English for photorealistic image generation."
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
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    parsed = json.loads(text)
    return _normalize_concepts(parsed, form)


def _fallback_concepts(form):
    product = form["product_name"].strip() or "产品"
    audience = form["audience"].strip() or "目标用户"
    selling_point = form["selling_point"].strip() or "核心卖点"
    ad_copy = form["ad_copy"].strip() or "Brand moment"
    aspect_ratio = form["aspect_ratio"]

    concepts = []
    for index, (theme, scene, angle) in enumerate(THEMES, start=1):
        prompt = (
            f"Photorealistic ad concept for {product}, theme: {theme}. "
            f"Scene: {scene} Target audience: {audience}. "
            f"Key selling points: {selling_point}. Copy overlay: {ad_copy}. "
            f"Aspect ratio: {aspect_ratio}. Premium commercial lighting, clean composition."
        )
        concepts.append(
            {
                "index": index,
                "theme": theme,
                "scene": scene,
                "angle": angle,
                "selling_point": selling_point,
                "prompt": prompt,
            }
        )
    return concepts


def _normalize_concepts(items, form):
    fallback = _fallback_concepts(form)
    concepts = []
    for index, item in enumerate(items[:10], start=1):
        backup = fallback[index - 1]
        concepts.append(
            {
                "index": index,
                "theme": item.get("theme") or backup["theme"],
                "scene": item.get("scene") or backup["scene"],
                "angle": item.get("angle") or backup["angle"],
                "selling_point": item.get("selling_point") or backup["selling_point"],
                "prompt": item.get("prompt") or backup["prompt"],
            }
        )
    return concepts or fallback
