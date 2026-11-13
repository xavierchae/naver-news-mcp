"""검색 API 요청 생성·응답 파싱·페이지네이션 상태. I/O 없음.

클라이언트는 `SearchSession.next_params()`가 None을 줄 때까지 요청을 보내고
응답 JSON을 `feed()`에 넣은 뒤 `result()`를 받는다. async·sync 클라이언트가 이 로직을 공유한다.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

from .errors import InvalidRequestError
from .filters import KST, assign_clusters, domain_matches, kst_date, normalize_domain
from .models import NewsItem, SearchResult

SEARCH_URL = "https://openapi.naver.com/v1/search/news.json"
PAGE_SIZE = 100  # API의 display 최댓값
MAX_START = 1000  # API의 start 최댓값

Sort = Literal["date", "sim"]
DateFrom = date | Literal["today"] | None

_NAVER_NEWS_HOSTS = ("news.naver.com", "entertain.naver.com", "sports.naver.com")
_BOLD_TAG = re.compile(r"</?b>")


def clean_text(value: str) -> str:
    """검색어 강조용 <b> 태그와 HTML 엔티티를 제거한다."""
    return html.unescape(_BOLD_TAG.sub("", value)).strip()


def is_naver_news_url(url: str) -> bool:
    return domain_matches(normalize_domain(url), _NAVER_NEWS_HOSTS)


def parse_search_response(data: dict[str, Any]) -> tuple[int, list[NewsItem]]:
    """검색 API 응답 JSON → (전체 건수, 기사 목록)."""
    items = []
    for raw in data.get("items", []):
        link = raw["link"]
        original_link = raw.get("originallink") or link
        published_at = parsedate_to_datetime(raw["pubDate"])
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=KST)
        items.append(
            NewsItem(
                title=clean_text(raw["title"]),
                description=clean_text(raw.get("description", "")),
                link=link,
                original_link=original_link,
                published_at=published_at,
                domain=normalize_domain(original_link),
                is_naver_news=is_naver_news_url(link),
            )
        )
    return int(data.get("total", 0)), items


class SearchSession:
    """한 번의 검색에 대한 페이지네이션·필터 상태."""

    def __init__(
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
        duplicate_threshold: float = 0.85,
        today: date | None = None,
    ) -> None:
        if not query or not query.strip():
            raise InvalidRequestError("검색어(query)가 비어 있습니다.")
        if max_results < 1:
            raise InvalidRequestError("max_results는 1 이상이어야 합니다.")
        if sort not in ("date", "sim"):
            raise InvalidRequestError("sort는 'date' 또는 'sim'이어야 합니다.")
        if date_from == "today":
            date_from = today or datetime.now(KST).date()
        if date_from and date_to and date_from > date_to:
            raise InvalidRequestError("date_from이 date_to보다 늦습니다.")

        self.query = query.strip()
        self.max_results = max_results
        self.date_from = date_from
        self.date_to = date_to
        # 기간 필터는 최신순으로 훑다가 범위를 벗어나면 멈추는 방식이라 date 정렬이 필요하다.
        self.sort: Sort = "date" if (date_from or date_to) else sort
        self._include = [normalize_domain(d) for d in include_domains or []]
        self._exclude = [normalize_domain(d) for d in exclude_domains or []]
        self._collapse = collapse_duplicates
        self._threshold = duplicate_threshold

        self._start = 1
        self._done = False
        self._seen: set[str] = set()
        self._items: list[NewsItem] = []
        self._total = 0
        self._scanned = 0
        self._has_more = False
        self._truncated = False

    def next_params(self) -> dict[str, Any] | None:
        """다음에 보낼 쿼리 파라미터. 더 보낼 요청이 없으면 None."""
        if self._done:
            return None
        return {
            "query": self.query,
            "display": PAGE_SIZE,
            "start": self._start,
            "sort": self.sort,
        }

    def _accept(self, item: NewsItem) -> bool:
        published = kst_date(item.published_at)
        if self.date_from and published < self.date_from:
            return False
        if self.date_to and published > self.date_to:
            return False
        if self._include and not domain_matches(item.domain, self._include):
            return False
        return not (self._exclude and domain_matches(item.domain, self._exclude))

    def feed(self, data: dict[str, Any]) -> None:
        """응답 JSON 한 페이지를 반영한다."""
        self._total, page = parse_search_response(data)
        for item in page:
            if item.link in self._seen:  # 같은 URL이 반복되면 한 번만
                continue
            self._seen.add(item.link)
            self._scanned += 1
            if not self._accept(item):
                continue
            if len(self._items) < self.max_results:
                self._items.append(item)
            else:
                self._has_more = True

        exhausted = len(page) < PAGE_SIZE or self._start + len(page) > self._total
        past_date_from = bool(
            self.date_from and page and kst_date(page[-1].published_at) < self.date_from
        )
        if len(self._items) >= self.max_results:
            if not (exhausted or past_date_from):
                self._has_more = True
            self._done = True
        elif exhausted or past_date_from:
            self._done = True
        elif self._start >= MAX_START:
            self._truncated = True
            self._done = True
        else:
            self._start = min(self._start + PAGE_SIZE, MAX_START)

    def result(self) -> SearchResult:
        items = self._items
        clusters = assign_clusters(items, self._threshold)
        if self._collapse:
            items = [item for item in items if item.is_representative]
        return SearchResult(
            query=self.query,
            total=self._total,
            items=items,
            clusters=clusters,
            date_from=self.date_from,
            date_to=self.date_to,
            scanned=self._scanned,
            has_more=self._has_more,
            truncated=self._truncated,
        )
