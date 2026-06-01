import csv
from io import BytesIO, StringIO

from app.models import CollectedNote


def build_notes_csv(platform):
    query = CollectedNote.query
    if platform and platform != "platform":
        query = query.filter_by(platform=platform)
    rows = query.order_by(CollectedNote.collection_time.desc()).all()

    text_buffer = StringIO()
    text_buffer.write("\ufeff")
    writer = csv.writer(text_buffer)
    writer.writerow(
        [
            "采集关键词",
            "平台",
            "标题",
            "作者",
            "点赞量",
            "评论量",
            "发布时间",
            "采集时间",
            "原文链接",
        ]
    )
    for note in rows:
        writer.writerow(
            [
                note.product_keyword or "",
                note.platform or "",
                note.title or "",
                note.author or "",
                note.likes_count,
                note.comments_count,
                note.publish_time.strftime("%Y-%m-%d %H:%M") if note.publish_time else "",
                note.collection_time.strftime("%Y-%m-%d %H:%M") if note.collection_time else "",
                note.source_url or "",
            ]
        )

    payload = text_buffer.getvalue().encode("utf-8-sig")
    return BytesIO(payload)
