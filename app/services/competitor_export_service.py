import csv
import json
from io import BytesIO, StringIO

from app.models import CompetitorProduct


def build_products_csv(task_id=None):
    query = CompetitorProduct.query
    if task_id:
        query = query.filter_by(task_id=task_id)
    rows = query.order_by(CompetitorProduct.collected_at.desc()).all()

    text_buffer = StringIO()
    text_buffer.write("\ufeff")
    writer = csv.writer(text_buffer)
    writer.writerow(["采集来源", "来源类型", "标题", "价格", "产品创建时间", "Product Tags", "评论数", "广告量", "产品链接", "采集时间"])
    for product in rows:
        writer.writerow(
            [
                product.source_domain,
                product.source_type,
                product.title or "",
                product.price or "",
                product.product_created_at.strftime("%Y-%m-%d %H:%M") if product.product_created_at else "",
                ", ".join(json.loads(product.product_tags or "[]")),
                product.reviews_count,
                product.fb_ad_count if product.fb_ad_count is not None else "",
                product.product_url or "",
                product.collected_at.strftime("%Y-%m-%d %H:%M") if product.collected_at else "",
            ]
        )

    return BytesIO(text_buffer.getvalue().encode("utf-8-sig"))
