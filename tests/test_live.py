"""실제 네이버 API·뉴스 페이지를 호출한다. `uv run pytest -m live`로만 실행된다."""

import pytest
from naver_news import AsyncNaverNewsClient
from naver_news_mcp.config import Settings

pytestmark = pytest.mark.live


@pytest.fixture
async def client():
    settings = Settings()  # 환경변수 또는 저장소 루트의 .env
    if not settings.client_id or not settings.client_secret:
        pytest.skip("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET가 없습니다.")
    async with AsyncNaverNewsClient(settings.client_id, settings.client_secret) as client:
        yield client


async def test_search_and_get_article(client):
    result = await client.search("경제", max_results=20, date_from=None)
    assert result.total > 0
    assert len(result.items) == 20
    assert all(item.cluster_id > 0 for item in result.items)

    naver_item = next(item for item in result.items if item.is_naver_news)
    article = await client.get_article(naver_item.link)
    assert article.content
    assert article.press


async def test_today_with_press_info(client):
    result = await client.search("삼성전자", max_results=5, include_press_info=True)
    assert result.date_from is not None
    assert any(item.press for item in result.items if item.is_naver_news)
