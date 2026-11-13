"""async·sync 클라이언트. 로직은 search/article/filters/transport에 있고 여기는 I/O만 맡는다."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Sequence
from datetime import date
from typing import Any

import httpx

from .article import parse_article
from .errors import ArticleExtractionError, AuthenticationError, NaverNewsError, TransportError
from .models import Article, NewsItem, SearchResult
from .search import SEARCH_URL, DateFrom, SearchSession, Sort, is_naver_news_url
from .transport import RetryPolicy, Throttle, error_from_response, is_retryable

# 앞의 이름이 우선한다.
ENV_CLIENT_ID = ("NAVER_CLIENT_ID", "NaverClientID")
ENV_CLIENT_SECRET = ("NAVER_CLIENT_SECRET", "NaverClientSecret")


def _first_env(names: tuple[str, ...]) -> str:
    return next((os.environ[name] for name in names if os.environ.get(name)), "")


# 네이버 뉴스 페이지는 브라우저가 아닌 User-Agent에 다른 응답을 줄 수 있다.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


class _ClientBase:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        api_requests_per_second: float = 10.0,
        page_interval: float = 0.2,
        page_concurrency: int = 3,
        duplicate_threshold: float = 0.85,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        if not client_id or not client_secret:
            raise AuthenticationError("네이버 API Client ID와 Client Secret이 필요합니다.")
        self._api_headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        }
        self._page_headers = {"User-Agent": user_agent}
        self._timeout = timeout
        self._retry = RetryPolicy(max_retries=max_retries)
        self._api_throttle = Throttle(1.0 / api_requests_per_second)
        self._page_throttle = Throttle(page_interval)
        self._page_concurrency = page_concurrency
        self._duplicate_threshold = duplicate_threshold

    @classmethod
    def from_env(cls, **kwargs: Any):
        """환경변수로 생성한다. (.env는 읽지 않는다)

        NAVER_CLIENT_ID / NAVER_CLIENT_SECRET, 없으면 NaverClientID / NaverClientSecret를 읽는다.
        """
        client_id = _first_env(ENV_CLIENT_ID)
        client_secret = _first_env(ENV_CLIENT_SECRET)
        if not client_id or not client_secret:
            raise AuthenticationError(
                "환경변수 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET"
                "(또는 NaverClientID, NaverClientSecret)를 설정하세요."
            )
        return cls(client_id, client_secret, **kwargs)

    def _session(self, query: str, options: dict[str, Any]) -> SearchSession:
        return SearchSession(query, duplicate_threshold=self._duplicate_threshold, **options)

    @staticmethod
    def _check_article_url(url: str) -> None:
        if not is_naver_news_url(url):
            raise ArticleExtractionError(
                f"네이버 뉴스 기사 URL만 본문을 조회할 수 있습니다: {url} "
                "(검색 결과에서 is_naver_news가 true인 기사의 link를 사용하세요)"
            )

    @staticmethod
    def _press_targets(result: SearchResult) -> list[NewsItem]:
        return [item for item in result.items if item.is_naver_news]

    @staticmethod
    def _apply_press(item: NewsItem, article: Article) -> None:
        item.press = article.press
        item.authors = article.authors


class AsyncNaverNewsClient(_ClientBase):
    """네이버 뉴스 검색·본문 조회 비동기 클라이언트.

    async with AsyncNaverNewsClient(client_id, client_secret) as client:
        result = await client.search("검색어")
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(client_id, client_secret, **kwargs)
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        self._page_semaphore = asyncio.Semaphore(self._page_concurrency)

    async def __aenter__(self) -> AsyncNaverNewsClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _get(
        self, url: str, throttle: Throttle, headers: dict[str, str], params: dict | None = None
    ) -> httpx.Response:
        attempt = 0
        while True:
            await asyncio.sleep(throttle.reserve())
            try:
                response = await self._http.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt >= self._retry.max_retries:
                    raise TransportError(f"요청에 실패했습니다: {url} ({exc!r})") from exc
            else:
                if response.is_success:
                    return response
                if attempt >= self._retry.max_retries or not is_retryable(
                    response.status_code, response.text
                ):
                    raise error_from_response(response.status_code, response.text)
            await asyncio.sleep(self._retry.delay(attempt))
            attempt += 1

    async def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        sort: Sort = "date",
        date_from: DateFrom = "today",
        date_to: date | None = None,
        include_domains: Sequence[str] | None = None,
        exclude_domains: Sequence[str] | None = None,
        collapse_duplicates: bool = False,
        include_press_info: bool = False,
    ) -> SearchResult:
        """뉴스를 검색한다.

        date_from 기본값 "today"는 오늘(KST) 발행 기사만 찾는다. None이면 기간 제한이 없다.
        기간 제한이 있으면 sort는 "date"로 강제된다.
        중복(유사 제목) 기사는 삭제하지 않고 cluster_id로 묶어 표시한다.
        """
        session = self._session(
            query,
            dict(
                max_results=max_results,
                sort=sort,
                date_from=date_from,
                date_to=date_to,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                collapse_duplicates=collapse_duplicates,
            ),
        )
        while (params := session.next_params()) is not None:
            response = await self._get(SEARCH_URL, self._api_throttle, self._api_headers, params)
            session.feed(response.json())
        result = session.result()
        if include_press_info:
            await asyncio.gather(*(self._fill_press(item) for item in self._press_targets(result)))
        return result

    async def _fill_press(self, item: NewsItem) -> None:
        # 언론사·기자 정보는 부가 정보라 실패해도 검색 결과는 돌려준다
        with contextlib.suppress(NaverNewsError):
            self._apply_press(item, await self.get_article(item.link))

    async def get_article(self, url: str) -> Article:
        """네이버 뉴스 기사 페이지에서 본문·언론사·기자를 추출한다."""
        self._check_article_url(url)
        async with self._page_semaphore:
            response = await self._get(url, self._page_throttle, self._page_headers)
        return parse_article(response.text, url)


class NaverNewsClient(_ClientBase):
    """네이버 뉴스 검색·본문 조회 동기 클라이언트. 메서드는 AsyncNaverNewsClient와 같다."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http_client: httpx.Client | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(client_id, client_secret, **kwargs)
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=self._timeout, follow_redirects=True)

    def __enter__(self) -> NaverNewsClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _get(
        self, url: str, throttle: Throttle, headers: dict[str, str], params: dict | None = None
    ) -> httpx.Response:
        attempt = 0
        while True:
            time.sleep(throttle.reserve())
            try:
                response = self._http.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt >= self._retry.max_retries:
                    raise TransportError(f"요청에 실패했습니다: {url} ({exc!r})") from exc
            else:
                if response.is_success:
                    return response
                if attempt >= self._retry.max_retries or not is_retryable(
                    response.status_code, response.text
                ):
                    raise error_from_response(response.status_code, response.text)
            time.sleep(self._retry.delay(attempt))
            attempt += 1

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        sort: Sort = "date",
        date_from: DateFrom = "today",
        date_to: date | None = None,
        include_domains: Sequence[str] | None = None,
        exclude_domains: Sequence[str] | None = None,
        collapse_duplicates: bool = False,
        include_press_info: bool = False,
    ) -> SearchResult:
        """뉴스를 검색한다. 인자의 의미는 AsyncNaverNewsClient.search와 같다."""
        session = self._session(
            query,
            dict(
                max_results=max_results,
                sort=sort,
                date_from=date_from,
                date_to=date_to,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                collapse_duplicates=collapse_duplicates,
            ),
        )
        while (params := session.next_params()) is not None:
            response = self._get(SEARCH_URL, self._api_throttle, self._api_headers, params)
            session.feed(response.json())
        result = session.result()
        if include_press_info:
            for item in self._press_targets(result):
                with contextlib.suppress(NaverNewsError):
                    self._apply_press(item, self.get_article(item.link))
        return result

    def get_article(self, url: str) -> Article:
        """네이버 뉴스 기사 페이지에서 본문·언론사·기자를 추출한다."""
        self._check_article_url(url)
        response = self._get(url, self._page_throttle, self._page_headers)
        return parse_article(response.text, url)
