from datetime import datetime, timedelta

import pytest
import respx
from mcp.client import Client
from naver_news.search import SEARCH_URL
from naver_news_mcp import server

from tests.conftest import KST, page, raw_item

NOW = datetime.now(KST)
ARTICLE_URL = "https://n.news.naver.com/mnews/article/001/0000000001"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """.env와 이전 테스트의 클라이언트가 섞이지 않게 한다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NaverClientID", raising=False)
    monkeypatch.delenv("NaverClientSecret", raising=False)
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")
    monkeypatch.setattr(server, "_client", None)


async def test_lists_tools():
    async with Client(server.mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"search_news", "get_article"}
    properties = tools["search_news"].input_schema["properties"]
    assert tools["search_news"].input_schema["required"] == ["query"]
    assert properties["date_from"]["default"] == "today"
    assert properties["collapse_duplicates"]["default"] is False
    assert properties["max_results"]["maximum"] == 100


@respx.mock
async def test_search_news_defaults_to_today():
    items = [raw_item(1, NOW), raw_item(2, NOW - timedelta(days=3))]
    respx.get(SEARCH_URL).respond(json=page(items))
    async with Client(server.mcp) as client:
        result = await client.call_tool("search_news", {"query": "인공지능"})
    assert result.is_error is False
    data = result.structured_content
    assert [item["link"] for item in data["items"]] == [items[0]["link"]]
    assert data["date_from"] == NOW.date().isoformat()
    assert data["clusters"][0]["count"] == 1


@respx.mock
async def test_search_news_all_dates_and_range():
    items = [raw_item(1, NOW), raw_item(2, NOW - timedelta(days=3))]
    route = respx.get(SEARCH_URL).respond(json=page(items))
    async with Client(server.mcp) as client:
        everything = await client.call_tool(
            "search_news", {"query": "q", "date_from": "all", "sort": "sim"}
        )
        ranged = await client.call_tool(
            "search_news",
            {
                "query": "q",
                "date_from": (NOW - timedelta(days=5)).date().isoformat(),
                "date_to": (NOW - timedelta(days=1)).date().isoformat(),
            },
        )
    assert route.calls[0].request.url.params["sort"] == "sim"
    assert len(everything.structured_content["items"]) == 2
    assert everything.structured_content["date_from"] is None
    assert [item["link"] for item in ranged.structured_content["items"]] == [items[1]["link"]]


@respx.mock
async def test_alternate_credential_names(monkeypatch):
    monkeypatch.delenv("NAVER_CLIENT_ID")
    monkeypatch.delenv("NAVER_CLIENT_SECRET")
    monkeypatch.setenv("NaverClientID", "alt-id")
    monkeypatch.setenv("NaverClientSecret", "alt-secret")
    route = respx.get(SEARCH_URL).respond(json=page([raw_item(1, NOW)]))
    async with Client(server.mcp) as client:
        result = await client.call_tool("search_news", {"query": "q"})
    assert result.is_error is False
    assert route.calls.last.request.headers["X-Naver-Client-Id"] == "alt-id"


async def test_bad_date_is_tool_error():
    async with Client(server.mcp) as client:
        result = await client.call_tool("search_news", {"query": "q", "date_from": "어제"})
    assert result.is_error is True
    assert "YYYY-MM-DD" in result.content[0].text


async def test_missing_credentials_is_tool_error(monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_ID", "")
    async with Client(server.mcp) as client:
        result = await client.call_tool("search_news", {"query": "q"})
    assert result.is_error is True
    assert "NAVER_CLIENT_ID" in result.content[0].text


@respx.mock
async def test_api_error_is_tool_error():
    respx.get(SEARCH_URL).respond(401, json={"errorMessage": "fail", "errorCode": "024"})
    async with Client(server.mcp) as client:
        result = await client.call_tool("search_news", {"query": "q"})
    assert result.is_error is True
    assert "인증" in result.content[0].text


@respx.mock
async def test_get_article_truncates(article_html):
    long_html = f'<div id="dic_area">{"가나다라마" * 100}</div>'
    other_url = ARTICLE_URL.replace("0000000001", "0000000002")
    respx.get(ARTICLE_URL).respond(text=article_html)
    respx.get(other_url).respond(text=long_html)
    async with Client(server.mcp) as client:
        full = await client.call_tool("get_article", {"url": ARTICLE_URL})
        short = await client.call_tool("get_article", {"url": other_url, "max_chars": 200})
    assert full.structured_content["press"] == "테스트일보"
    assert "생략" not in full.structured_content["content"]
    assert short.structured_content["content"] == "가나다라마" * 40 + "\n…(이하 생략, 전체 500자)"


async def test_get_article_rejects_non_naver_url():
    async with Client(server.mcp) as client:
        result = await client.call_tool("get_article", {"url": "https://example.com/news/1"})
    assert result.is_error is True
    assert "네이버 뉴스" in result.content[0].text
