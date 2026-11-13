"""네이버 뉴스 기사 페이지 HTML 파싱. I/O 없음.

네이버 마크업에 의존하는 셀렉터는 모두 이 파일에 둔다.
"""

from __future__ import annotations

import re
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .errors import ArticleExtractionError
from .filters import KST
from .models import Article

SEL_TITLE = ("#title_area", "h2.media_end_head_headline")
SEL_PRESS_LOGO = ".media_end_head_top_logo img"
SEL_PRESS_NAME = ".media_end_head_top_press"
SEL_AUTHORS = ".media_end_head_journalist_name"
SEL_BYLINE = ".byline_s"
SEL_PUBLISHED = "._ARTICLE_DATE_TIME"
SEL_MODIFIED = "._ARTICLE_MODIFY_DATE_TIME"
SEL_BODY = ("#dic_area", "#newsct_article")
# 본문 안의 사진·캡션·영상·스크립트 등 기사 텍스트가 아닌 요소
SEL_BODY_NOISE = (
    "script, style, .end_photo_org, .img_desc, .vod_player_wrap, .video_area, "
    ".artice_img, .media_end_linked_more, .byline, .reporter_area, .copyright"
)
_BLOCK_TAGS = "p, div, li, h1, h2, h3, h4, h5, h6, tr, blockquote"

_BLANK_LINES = re.compile(r"\n{3,}")


def _first(tree: LexborHTMLParser, selectors: tuple[str, ...]) -> LexborNode | None:
    for selector in selectors:
        node = tree.css_first(selector)
        if node is not None:
            return node
    return None


def _meta(tree: LexborHTMLParser, prop: str) -> str | None:
    node = tree.css_first(f'meta[property="{prop}"]')
    return (node.attributes.get("content") or None) if node is not None else None


def _parse_time(tree: LexborHTMLParser, selector: str, attr: str) -> datetime | None:
    node = tree.css_first(selector)
    value = node.attributes.get(attr) if node is not None else None
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return None


def _press(tree: LexborHTMLParser) -> str | None:
    logo = tree.css_first(SEL_PRESS_LOGO)
    if logo is not None and (logo.attributes.get("alt") or logo.attributes.get("title")):
        return logo.attributes.get("alt") or logo.attributes.get("title")
    name = tree.css_first(SEL_PRESS_NAME)
    if name is not None and name.text(strip=True):
        return name.text(strip=True)
    author = _meta(tree, "og:article:author")  # "뉴시스 | 네이버"
    return author.split("|")[0].strip() or None if author else None


def _authors(tree: LexborHTMLParser) -> list[str]:
    names = [node.text(strip=True) for node in tree.css(SEL_AUTHORS)]
    if not names:
        names = [node.text(strip=True) for node in tree.css(SEL_BYLINE)]
    return list(dict.fromkeys(name for name in names if name))


def _body_text(body: LexborNode) -> str:
    for node in body.css(SEL_BODY_NOISE):
        node.decompose()
    for node in body.css("br"):
        node.replace_with("\n")
    for node in body.css(_BLOCK_TAGS):
        node.insert_before("\n")
        node.insert_after("\n")
    lines = [line.strip() for line in body.text(deep=True, separator="").splitlines()]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def parse_article(html: str, url: str) -> Article:
    """기사 페이지 HTML → Article. 본문을 찾지 못하면 ArticleExtractionError."""
    tree = LexborHTMLParser(html)
    body = _first(tree, SEL_BODY)
    content = _body_text(body) if body is not None else ""
    if not content:
        raise ArticleExtractionError(
            f"기사 본문을 찾지 못했습니다: {url} "
            "(연예·스포츠 기사이거나 네이버 페이지 구조가 바뀌었을 수 있습니다)"
        )
    title_node = _first(tree, SEL_TITLE)
    title = title_node.text(strip=True) if title_node is not None else ""
    return Article(
        url=url,
        title=title or _meta(tree, "og:title") or "",
        press=_press(tree),
        authors=_authors(tree),
        published_at=_parse_time(tree, SEL_PUBLISHED, "data-date-time"),
        modified_at=_parse_time(tree, SEL_MODIFIED, "data-modify-date-time"),
        content=content,
    )
