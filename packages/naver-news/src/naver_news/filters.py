"""기간·도메인 필터와 중복 기사 묶음 판정. I/O 없는 순수 함수."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from .models import NewsCluster, NewsItem

KST = timezone(timedelta(hours=9))

# [속보], (종합), 【단독】 같은 말머리
_TAG = re.compile(r"\[[^\]]{1,15}\]|\([^)]{1,15}\)|【[^】]{1,15}】|<[^>]{1,15}>")
_NON_WORD = re.compile(r"[\W_]+")


def kst_date(dt: datetime) -> date:
    return dt.astimezone(KST).date()


def normalize_domain(value: str) -> str:
    """URL 또는 도메인 문자열에서 소문자 호스트를 얻고 www./m. 접두를 뗀다."""
    value = value.strip().lower()
    host = urlsplit(value).hostname if "://" in value else value.split("/")[0]
    host = host or ""
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host


def domain_matches(domain: str, patterns: Sequence[str]) -> bool:
    """domain이 patterns 중 하나와 같거나 그 하위 도메인이면 True."""
    return any(domain == p or domain.endswith("." + p) for p in patterns)


def normalize_title(title: str) -> str:
    """유사도 비교용 제목: 말머리·공백·문장부호 제거, 소문자화."""
    stripped = _NON_WORD.sub("", _TAG.sub(" ", title)).lower()
    return stripped or _NON_WORD.sub("", title).lower()


def _similar(a: str, b: str, threshold: float) -> bool:
    if a == b:
        return True
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    return (
        matcher.real_quick_ratio() >= threshold
        and matcher.quick_ratio() >= threshold
        and matcher.ratio() >= threshold
    )


def assign_clusters(items: list[NewsItem], threshold: float = 0.85) -> list[NewsCluster]:
    """제목이 유사한 기사끼리 묶어 각 item에 cluster_id·is_representative를 기록한다.

    item은 삭제하지 않는다. 묶음 번호는 items에 처음 등장하는 순서대로 1부터 매기고,
    묶음의 대표는 가장 먼저 발행된 기사다.
    """
    chronological = sorted(range(len(items)), key=lambda i: items[i].published_at)
    groups: list[tuple[str, list[int]]] = []
    for i in chronological:
        key = normalize_title(items[i].title)
        for rep_key, members in groups:
            if _similar(key, rep_key, threshold):
                members.append(i)
                break
        else:
            groups.append((key, [i]))

    groups.sort(key=lambda g: min(g[1]))
    clusters: list[NewsCluster] = []
    for cluster_id, (_, members) in enumerate(groups, start=1):
        for i in members:
            items[i].cluster_id = cluster_id
            items[i].is_representative = i == members[0]
        clusters.append(
            NewsCluster(
                cluster_id=cluster_id,
                title=items[members[0]].title,
                count=len(members),
                domains=list(dict.fromkeys(items[i].domain for i in members)),
                first_published_at=items[members[0]].published_at,
                last_published_at=items[members[-1]].published_at,
            )
        )
    return clusters
