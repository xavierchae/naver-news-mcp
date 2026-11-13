"""서버 설정. 프로세스 환경변수가 .env(현재 작업 디렉터리)보다 우선한다.

인증 정보는 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET,
없으면 NaverClientID / NaverClientSecret를 읽는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    client_id: str = Field(
        default="", validation_alias=AliasChoices("NAVER_CLIENT_ID", "NaverClientID")
    )
    client_secret: str = Field(
        default="", validation_alias=AliasChoices("NAVER_CLIENT_SECRET", "NaverClientSecret")
    )
    timeout: float = Field(default=10.0, validation_alias="NAVER_NEWS_TIMEOUT")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", validation_alias="NAVER_NEWS_LOG_LEVEL"
    )
