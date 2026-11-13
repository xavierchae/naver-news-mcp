"""재시도·rate limit 정책과 오류 매핑. 대기(sleep)와 HTTP 호출은 클라이언트가 한다."""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass

from .errors import (
    AuthenticationError,
    InvalidRequestError,
    NaverNewsError,
    QuotaExceededError,
    TransportError,
)

_QUOTA_ERROR_CODE = "010"  # 일일 한도 초과. 같은 429라도 초당 한도 초과(012)는 재시도 대상


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    def delay(self, attempt: int) -> float:
        """attempt(0부터)번째 실패 후 대기 시간: 지수 백오프 + 지터."""
        return min(self.max_delay, self.base_delay * 2**attempt) * (0.5 + random.random() / 2)


class Throttle:
    """요청 간 최소 간격을 보장한다. `reserve()`가 돌려준 시간만큼 호출자가 쉰다."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._next_slot = 0.0

    def reserve(self) -> float:
        now = time.monotonic()
        slot = max(now, self._next_slot)
        self._next_slot = slot + self._min_interval
        return slot - now


def is_retryable(status_code: int, body: str) -> bool:
    if status_code == 429:
        return _error_fields(body)[0] != _QUOTA_ERROR_CODE
    return status_code >= 500


def error_from_response(status_code: int, body: str) -> NaverNewsError:
    """실패 응답을 예외로 바꾼다."""
    code, message = _error_fields(body)
    detail = (
        f"HTTP {status_code}" + (f", {code}" if code else "") + (f": {message}" if message else "")
    )
    if status_code == 401:
        return AuthenticationError(
            f"네이버 API 인증에 실패했습니다 ({detail}). Client ID/Secret을 확인하세요."
        )
    if status_code == 403:
        return AuthenticationError(
            f"네이버 API 사용 권한이 없습니다 ({detail}). "
            "개발자센터에서 애플리케이션의 '검색' API 사용 설정을 확인하세요."
        )
    if status_code == 429 and code == _QUOTA_ERROR_CODE:
        return QuotaExceededError(f"네이버 검색 API 일일 호출 한도를 넘었습니다 ({detail}).")
    if status_code == 429:
        return TransportError(f"네이버 API 호출 속도 제한에 걸렸습니다 ({detail}).")
    if 400 <= status_code < 500:
        return InvalidRequestError(f"요청이 거부됐습니다 ({detail}).")
    return TransportError(f"서버 오류가 계속됩니다 ({detail}).")


def _error_fields(body: str) -> tuple[str, str]:
    try:
        data = json.loads(body)
    except ValueError:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return str(data.get("errorCode", "")), str(data.get("errorMessage", ""))
