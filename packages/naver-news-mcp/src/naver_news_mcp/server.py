"""네이버 뉴스 MCP 서버 (stdio)."""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from naver_news import Article, AsyncNaverNewsClient, NaverNewsError, SearchResult
from pydantic import Field

from .config import Settings

logger = logging.getLogger("naver_news_mcp")

_client: AsyncNaverNewsClient | None = None


def _get_client() -> AsyncNaverNewsClient:
    """클라이언트를 처음 쓸 때 만든다. 인증 정보가 없어도 서버는 뜨고 tool 호출에서 안내한다."""
    global _client
    if _client is None:
        settings = Settings()
        if not settings.client_id or not settings.client_secret:
            raise ToolError(
                "네이버 API 인증 정보가 없습니다. 환경변수 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET"
                "(또는 NaverClientID, NaverClientSecret)를 MCP 서버 설정의 env, 셸 환경변수, "
                "또는 서버 작업 디렉터리의 .env 파일에 설정하세요."
            )
        _client = AsyncNaverNewsClient(
            settings.client_id, settings.client_secret, timeout=settings.timeout
        )
    return _client


@asynccontextmanager
async def _lifespan(_: MCPServer) -> AsyncIterator[None]:
    global _client
    try:
        yield
    finally:
        if _client is not None:
            await _client.aclose()
            _client = None


mcp = MCPServer(
    "naver-news",
    instructions=(
        "네이버 뉴스를 검색(search_news)하고 기사 본문을 조회(get_article)한다. "
        "search_news는 기본적으로 오늘(KST) 발행된 기사만 찾는다. 다른 기간은 date_from을 지정한다."
    ),
    version="0.1.0",
    lifespan=_lifespan,
)


def _parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{name}은 YYYY-MM-DD 형식이어야 합니다: {value!r}") from None


@mcp.tool()
async def search_news(
    query: Annotated[str, Field(description="검색어")],
    max_results: Annotated[
        int, Field(ge=1, le=100, description="반환할 기사 수 (개별 기사 기준)")
    ] = 10,
    sort: Annotated[
        Literal["date", "sim"],
        Field(description="date=최신순, sim=정확도순. sim은 date_from='all'일 때만 적용된다"),
    ] = "date",
    date_from: Annotated[
        str,
        Field(description="발행일 하한(KST). 'today'(기본) | YYYY-MM-DD | 'all'(기간 제한 없음)"),
    ] = "today",
    date_to: Annotated[
        str | None, Field(description="발행일 상한(KST), YYYY-MM-DD. 생략하면 현재까지")
    ] = None,
    include_domains: Annotated[
        list[str] | None, Field(description="이 언론사 도메인의 기사만 (예: ['yna.co.kr'])")
    ] = None,
    exclude_domains: Annotated[
        list[str] | None, Field(description="이 언론사 도메인의 기사는 제외")
    ] = None,
    collapse_duplicates: Annotated[
        bool,
        Field(description="true면 유사 제목 묶음마다 대표 기사만 반환 (건수는 clusters에 유지)"),
    ] = False,
    include_press_info: Annotated[
        bool,
        Field(description="true면 네이버 입점 기사의 언론사명·기자를 채운다. 기사 수만큼 느려진다"),
    ] = False,
) -> SearchResult:
    """네이버 뉴스를 검색한다. 기본값은 오늘(KST) 발행된 기사만 최신순으로 찾는다.

    같은 보도자료를 여러 언론사가 낸 경우처럼 제목이 유사한 기사는 삭제하지 않고
    cluster_id로 묶어 표시하며, clusters에 묶음별 기사 수와 언론사 도메인을 요약한다.
    has_more가 true면 max_results를 늘려야 전체 건수를 알 수 있다.
    본문은 is_naver_news가 true인 기사의 link를 get_article에 넘겨 조회한다.
    """
    if date_from == "all":
        resolved_from: date | Literal["today"] | None = None
    elif date_from == "today":
        resolved_from = "today"
    else:
        resolved_from = _parse_date(date_from, "date_from")
    try:
        return await _get_client().search(
            query,
            max_results=max_results,
            sort=sort,
            date_from=resolved_from,
            date_to=_parse_date(date_to, "date_to") if date_to else None,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            collapse_duplicates=collapse_duplicates,
            include_press_info=include_press_info,
        )
    except NaverNewsError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def get_article(
    url: Annotated[str, Field(description="search_news 결과의 link (네이버 뉴스 기사 URL)")],
    max_chars: Annotated[int, Field(ge=200, description="본문 길이 상한(자)")] = 8000,
) -> Article:
    """네이버 뉴스 기사의 본문·언론사·기자·발행 시각을 조회한다.

    네이버에 입점한 언론사의 기사(n.news.naver.com)만 지원한다.
    """
    try:
        article = await _get_client().get_article(url)
    except NaverNewsError as exc:
        raise ToolError(str(exc)) from exc
    if len(article.content) > max_chars:
        total = len(article.content)
        article.content = article.content[:max_chars] + f"\n…(이하 생략, 전체 {total:,}자)"
    return article


def main() -> None:
    # stdout은 stdio 프로토콜 전용이므로 로그는 stderr로만 보낸다.
    logging.basicConfig(
        level=Settings().log_level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx는 요청마다 INFO 로그를 남기므로 DEBUG로 볼 때만 보이게 한다.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
