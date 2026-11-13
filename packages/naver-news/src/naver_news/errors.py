"""naver-news 예외 계층."""

from __future__ import annotations


class NaverNewsError(Exception):
    """모든 naver-news 예외의 기반 클래스."""


class AuthenticationError(NaverNewsError):
    """Client ID/Secret이 없거나 틀렸거나, 검색 API 사용 권한이 없다."""


class QuotaExceededError(NaverNewsError):
    """검색 API 일일 호출 한도(25,000건)를 넘었다."""


class InvalidRequestError(NaverNewsError):
    """요청 인자가 잘못됐다."""


class ArticleExtractionError(NaverNewsError):
    """기사 페이지에서 본문을 추출하지 못했다."""


class TransportError(NaverNewsError):
    """네트워크 오류, 또는 재시도 후에도 실패한 서버 오류."""
