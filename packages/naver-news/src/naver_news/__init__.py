"""네이버 검색 API 기반 뉴스 검색·본문 조회 클라이언트."""

from .client import AsyncNaverNewsClient, NaverNewsClient
from .errors import (
    ArticleExtractionError,
    AuthenticationError,
    InvalidRequestError,
    NaverNewsError,
    QuotaExceededError,
    TransportError,
)
from .models import Article, NewsCluster, NewsItem, SearchResult

__all__ = [
    "Article",
    "ArticleExtractionError",
    "AsyncNaverNewsClient",
    "AuthenticationError",
    "InvalidRequestError",
    "NaverNewsClient",
    "NaverNewsError",
    "NewsCluster",
    "NewsItem",
    "QuotaExceededError",
    "SearchResult",
    "TransportError",
]
