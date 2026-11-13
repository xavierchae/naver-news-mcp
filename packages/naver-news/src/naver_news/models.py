"""공개 데이터 모델."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class NewsItem(BaseModel):
    """검색 결과의 기사 한 건."""

    title: str
    description: str
    link: str = Field(description="네이버 뉴스 URL. 네이버 미입점 기사는 언론사 원문 URL")
    original_link: str = Field(description="언론사 원문 URL")
    published_at: datetime
    domain: str = Field(description="언론사 원문 도메인 (www. 제외)")
    is_naver_news: bool = Field(
        description="link가 네이버 뉴스 페이지인지. True여야 본문 조회 가능"
    )
    cluster_id: int = Field(default=0, description="중복(유사 제목) 기사 묶음 번호")
    is_representative: bool = Field(default=True, description="묶음에서 가장 먼저 발행된 기사인지")
    press: str | None = Field(default=None, description="언론사명. include_press_info 사용 시에만")
    authors: list[str] = Field(
        default_factory=list, description="기자. include_press_info 사용 시에만"
    )


class NewsCluster(BaseModel):
    """제목이 유사한 기사 묶음의 요약."""

    cluster_id: int
    title: str = Field(description="대표 기사 제목")
    count: int = Field(description="묶음에 속한 기사 수 (반환된 기사 범위 안에서 집계)")
    domains: list[str] = Field(description="기사를 낸 언론사 도메인 (발행순)")
    first_published_at: datetime
    last_published_at: datetime


class SearchResult(BaseModel):
    query: str
    total: int = Field(
        description="네이버 API가 보고한 검색어 전체 건수 (기간·도메인 필터 적용 전)"
    )
    items: list[NewsItem]
    clusters: list[NewsCluster]
    date_from: date | None = Field(description="적용된 발행일 하한 (KST). None이면 제한 없음")
    date_to: date | None = Field(description="적용된 발행일 상한 (KST). None이면 제한 없음")
    scanned: int = Field(description="필터 적용 전 훑어본 기사 수")
    has_more: bool = Field(description="max_results에 걸려 조건에 맞는 기사가 더 남아 있을 수 있음")
    truncated: bool = Field(
        description="API 조회 한도(질의당 약 1,100건)에 걸려 기간 끝까지 훑지 못함"
    )


class Article(BaseModel):
    """네이버 뉴스 기사 페이지에서 추출한 내용."""

    url: str
    title: str
    press: str | None = None
    authors: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    modified_at: datetime | None = None
    content: str
