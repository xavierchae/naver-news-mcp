from datetime import datetime

import pytest
from naver_news.article import parse_article
from naver_news.errors import ArticleExtractionError

from tests.conftest import KST

URL = "https://n.news.naver.com/mnews/article/001/0000000001"


def test_parse_article(article_html):
    article = parse_article(article_html, URL)
    assert article.url == URL
    assert article.title == "테스트바이오, 신약 후보물질 임상 1상 승인"
    assert article.press == "테스트일보"
    assert article.authors == ["홍길동 기자", "김철수 기자"]
    assert article.published_at == datetime(2026, 11, 13, 10, 6, 23, tzinfo=KST)
    assert article.modified_at == datetime(2026, 11, 13, 10, 37, 16, tzinfo=KST)
    assert article.content.split("\n") == [
        "임상 1상 계획 승인",
        "내년 상반기 투약 개시",
        "",
        "[서울=테스트일보] 홍길동 기자 = 테스트바이오는 신약 후보물질 TB-101의 임상 1상 시험계획을 승인받았다고 13일 밝혔다.",
        "",
        "회사는 내년 상반기 첫 환자 투약을 시작할 계획이다.",
        "",
        "향후 일정",
        '회사 관계자는 "글로벌 임상으로 확대하겠다"고 말했다.',
    ]


def test_caption_and_script_are_removed(article_html):
    content = parse_article(article_html, URL).content
    assert "사진 캡션" not in content
    assert "tracking" not in content


def test_fallbacks_when_header_markup_is_missing():
    html = """<html><head>
    <meta property="og:title" content="메타 제목">
    <meta property="og:article:author" content="메타일보 | 네이버">
    </head><body><div id="dic_area">본문입니다.</div>
    <span class="byline_s">이영희 기자(lee@example.com)</span></body></html>"""
    article = parse_article(html, URL)
    assert article.title == "메타 제목"
    assert article.press == "메타일보"
    assert article.authors == ["이영희 기자(lee@example.com)"]
    assert article.published_at is None
    assert article.content == "본문입니다."


def test_missing_body_raises():
    with pytest.raises(ArticleExtractionError):
        parse_article("<html><body><div id='app'></div></body></html>", URL)
