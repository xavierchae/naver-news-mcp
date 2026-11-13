from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

KST = timezone(timedelta(hours=9))
FIXTURES = Path(__file__).parent / "fixtures"


def raw_item(
    n: int,
    published: datetime,
    *,
    title: str | None = None,
    domain: str = "example-press.co.kr",
    naver: bool = True,
) -> dict:
    """검색 API 응답의 item 한 건."""
    original = f"https://www.{domain}/news/{n}"
    return {
        "title": title or f"기사 <b>제목</b> {n} &quot;인용&quot;",
        "originallink": original,
        "link": f"https://n.news.naver.com/mnews/article/001/{n:010d}" if naver else original,
        "description": f"요약 <b>내용</b> {n} &amp; 기타",
        "pubDate": format_datetime(published),
    }


def page(items: list[dict], total: int | None = None, start: int = 1) -> dict:
    return {
        "lastBuildDate": "Fri, 13 Nov 2026 12:00:00 +0900",
        "total": len(items) if total is None else total,
        "start": start,
        "display": len(items),
        "items": items,
    }


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "article.html").read_text(encoding="utf-8")
