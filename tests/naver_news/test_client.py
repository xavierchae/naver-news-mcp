from datetime import datetime

import httpx
import pytest
import respx
from naver_news import (
    ArticleExtractionError,
    AsyncNaverNewsClient,
    AuthenticationError,
    InvalidRequestError,
    NaverNewsClient,
    QuotaExceededError,
    TransportError,
)
from naver_news.search import SEARCH_URL

from tests.conftest import KST, page, raw_item

NOW = datetime.now(KST)
FAST = dict(api_requests_per_second=10_000, page_interval=0)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr("naver_news.transport.RetryPolicy.delay", lambda self, attempt: 0)


def error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"errorMessage": "msg", "errorCode": code})


@respx.mock
async def test_async_search_sends_credentials_and_parses():
    route = respx.get(SEARCH_URL).respond(json=page([raw_item(1, NOW), raw_item(2, NOW)]))
    async with AsyncNaverNewsClient("id", "secret", **FAST) as client:
        result = await client.search("인공지능")
    request = route.calls.last.request
    assert request.headers["X-Naver-Client-Id"] == "id"
    assert request.headers["X-Naver-Client-Secret"] == "secret"
    assert request.url.params["query"] == "인공지능"
    assert len(result.items) == 2


@respx.mock
def test_sync_search():
    respx.get(SEARCH_URL).respond(json=page([raw_item(1, NOW)]))
    with NaverNewsClient("id", "secret", **FAST) as client:
        result = client.search("q")
    assert len(result.items) == 1


@respx.mock
async def test_retries_server_error_and_rate_limit():
    route = respx.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(500),
            error(429, "012"),
            httpx.ConnectError("boom"),
            httpx.Response(200, json=page([raw_item(1, NOW)])),
        ]
    )
    async with AsyncNaverNewsClient("id", "secret", **FAST) as client:
        result = await client.search("q")
    assert route.call_count == 4
    assert len(result.items) == 1


@respx.mock
async def test_gives_up_after_max_retries():
    route = respx.get(SEARCH_URL).respond(503)
    async with AsyncNaverNewsClient("id", "secret", max_retries=2, **FAST) as client:
        with pytest.raises(TransportError):
            await client.search("q")
    assert route.call_count == 3


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (error(401, "024"), AuthenticationError),
        (error(403, "403"), AuthenticationError),
        (error(429, "010"), QuotaExceededError),
        (error(400, "SE01"), InvalidRequestError),
    ],
)
@respx.mock
async def test_errors_are_mapped_without_retry(response, expected):
    route = respx.get(SEARCH_URL).mock(return_value=response)
    async with AsyncNaverNewsClient("id", "secret", **FAST) as client:
        with pytest.raises(expected):
            await client.search("q")
    assert route.call_count == 1


@respx.mock
async def test_get_article(article_html):
    url = "https://n.news.naver.com/mnews/article/001/0000000001"
    respx.get(url).respond(text=article_html)
    async with AsyncNaverNewsClient("id", "secret", **FAST) as client:
        article = await client.get_article(url)
    assert article.press == "테스트일보"


async def test_get_article_rejects_non_naver_url():
    async with AsyncNaverNewsClient("id", "secret", **FAST) as client:
        with pytest.raises(ArticleExtractionError):
            await client.get_article("https://www.example-press.co.kr/news/1")


@respx.mock
async def test_include_press_info_fills_naver_items_only(article_html):
    items = [raw_item(1, NOW), raw_item(2, NOW, naver=False), raw_item(3, NOW)]
    respx.get(SEARCH_URL).respond(json=page(items))
    respx.get(items[0]["link"]).respond(text=article_html)
    respx.get(items[2]["link"]).respond(404)  # 실패해도 검색 결과는 반환
    async with AsyncNaverNewsClient("id", "secret", **FAST) as client:
        result = await client.search("q", include_press_info=True)
    first, second, third = result.items
    assert (first.press, first.authors) == ("테스트일보", ["홍길동 기자", "김철수 기자"])
    assert (second.press, second.authors) == (None, [])
    assert (third.press, third.authors) == (None, [])


@respx.mock
def test_sync_include_press_info(article_html):
    items = [raw_item(1, NOW)]
    respx.get(SEARCH_URL).respond(json=page(items))
    respx.get(items[0]["link"]).respond(text=article_html)
    with NaverNewsClient("id", "secret", **FAST) as client:
        result = client.search("q", include_press_info=True)
    assert result.items[0].press == "테스트일보"


ENV_NAMES = ["NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "NaverClientID", "NaverClientSecret"]


def test_from_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(AuthenticationError):
        NaverNewsClient.from_env()
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    NaverNewsClient.from_env().close()


def test_from_env_accepts_alternate_names(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NaverClientID", "alt-id")
    monkeypatch.setenv("NaverClientSecret", "alt-secret")
    with NaverNewsClient.from_env() as client:
        assert client._api_headers["X-Naver-Client-Id"] == "alt-id"
