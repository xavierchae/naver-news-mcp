from datetime import date, datetime, timedelta

import pytest
from naver_news.errors import InvalidRequestError
from naver_news.search import SearchSession, clean_text, parse_search_response

from tests.conftest import KST, page, raw_item

TODAY = date(2026, 11, 13)
NOON = datetime(2026, 11, 13, 12, 0, tzinfo=KST)


def run(session: SearchSession, pages: list[dict]) -> list[dict]:
    """세션이 요청하는 파라미터를 기록하며 준비된 페이지를 차례로 넣는다."""
    sent = []
    pages = iter(pages)
    while (params := session.next_params()) is not None:
        sent.append(params)
        session.feed(next(pages))
    return sent


def test_clean_text_strips_bold_and_entities():
    assert (
        clean_text("<b>인공</b>지능 &quot;신약&quot; &lt;인터뷰&gt;") == '인공지능 "신약" <인터뷰>'
    )


def test_parse_search_response():
    total, items = parse_search_response(
        page([raw_item(1, NOON), raw_item(2, NOON, naver=False)], total=57)
    )
    assert total == 57
    first, second = items
    assert first.title == '기사 제목 1 "인용"'
    assert first.description == "요약 내용 1 & 기타"
    assert first.domain == "example-press.co.kr"
    assert first.published_at == NOON
    assert first.is_naver_news is True
    assert second.is_naver_news is False
    assert second.link == second.original_link


def test_default_period_is_today_and_stops_at_older_page():
    yesterday = NOON - timedelta(days=1)
    first = [raw_item(i, NOON - timedelta(minutes=i)) for i in range(100)]
    second = [raw_item(100 + i, NOON - timedelta(minutes=100 + i)) for i in range(50)]
    second += [raw_item(200 + i, yesterday - timedelta(minutes=i)) for i in range(50)]
    session = SearchSession("q", max_results=1000, sort="sim", today=TODAY)
    sent = run(session, [page(first, total=5000), page(second, total=5000, start=101)])

    assert [p["start"] for p in sent] == [1, 101]
    assert all(p["sort"] == "date" and p["display"] == 100 for p in sent)  # 기간이 있으면 date 강제
    result = session.result()
    assert len(result.items) == 150
    assert result.date_from == TODAY
    assert result.scanned == 200
    assert result.truncated is False
    assert result.has_more is False


def test_today_boundary_uses_kst():
    just_after_midnight = datetime(2026, 11, 13, 0, 5, tzinfo=KST)
    just_before_midnight = datetime(2026, 11, 12, 23, 55, tzinfo=KST)
    session = SearchSession("q", today=TODAY)
    run(session, [page([raw_item(1, just_after_midnight), raw_item(2, just_before_midnight)])])
    assert [i.published_at for i in session.result().items] == [just_after_midnight]


def test_no_period_keeps_requested_sort():
    session = SearchSession("q", sort="sim", date_from=None)
    sent = run(session, [page([raw_item(1, NOON - timedelta(days=400))])])
    assert sent[0]["sort"] == "sim"
    assert len(session.result().items) == 1


def test_date_range():
    items = [raw_item(d, datetime(2026, 11, d, 9, 0, tzinfo=KST)) for d in (12, 10, 8, 5)]
    session = SearchSession("q", date_from=date(2026, 11, 7), date_to=date(2026, 11, 10))
    run(session, [page(items)])
    assert [i.published_at.day for i in session.result().items] == [10, 8]


def test_max_results_sets_has_more():
    items = [raw_item(i, NOON - timedelta(minutes=i)) for i in range(100)]
    session = SearchSession("q", max_results=10, today=TODAY)
    sent = run(session, [page(items, total=5000)])
    assert len(sent) == 1
    result = session.result()
    assert len(result.items) == 10
    assert result.has_more is True


def test_truncated_at_api_limit():
    def make(start: int) -> dict:
        return page(
            [raw_item(start + i, NOON - timedelta(seconds=start + i)) for i in range(100)],
            total=50000,
            start=start,
        )

    session = SearchSession("q", max_results=5000, today=TODAY)
    starts = [1, 101, 201, 301, 401, 501, 601, 701, 801, 901, 1000]
    sent = run(session, [make(s) for s in starts])
    assert [p["start"] for p in sent] == starts
    result = session.result()
    assert result.truncated is True
    assert result.scanned == 1099  # start=901과 1000 페이지가 한 건 겹친다
    assert len(result.items) == 1099


def test_domain_filters():
    items = [
        raw_item(1, NOON, domain="yna.co.kr"),
        raw_item(2, NOON, domain="news.example.com"),
        raw_item(3, NOON, domain="other.kr"),
    ]
    include = SearchSession(
        "q", include_domains=["https://www.yna.co.kr/", "example.com"], today=TODAY
    )
    run(include, [page(items)])
    assert [i.domain for i in include.result().items] == ["yna.co.kr", "news.example.com"]

    exclude = SearchSession("q", exclude_domains=["yna.co.kr"], today=TODAY)
    run(exclude, [page(items)])
    assert [i.domain for i in exclude.result().items] == ["news.example.com", "other.kr"]


def test_duplicates_are_kept_and_clustered():
    release = "테스트바이오, 신약 후보물질 임상 1상 승인"
    items = [
        raw_item(1, NOON, title=f"[속보] {release}", domain="c.kr"),
        raw_item(
            2, NOON - timedelta(minutes=5), title="전혀 다른 주제의 기사입니다", domain="x.kr"
        ),
        raw_item(3, NOON - timedelta(minutes=10), title=f"{release}(종합)", domain="b.kr"),
        raw_item(4, NOON - timedelta(minutes=20), title=release, domain="a.kr"),
    ]
    session = SearchSession("q", today=TODAY)
    run(session, [page(items)])
    result = session.result()

    assert len(result.items) == 4  # 중복이어도 개별 record는 모두 남는다
    assert [i.cluster_id for i in result.items] == [1, 2, 1, 1]
    assert [i.is_representative for i in result.items] == [False, True, False, True]
    release_cluster, other = result.clusters
    assert release_cluster.count == 3
    assert release_cluster.title == release  # 가장 먼저 발행된 기사가 대표
    assert release_cluster.domains == ["a.kr", "b.kr", "c.kr"]
    assert release_cluster.first_published_at == NOON - timedelta(minutes=20)
    assert release_cluster.last_published_at == NOON
    assert other.count == 1


def test_collapse_duplicates_keeps_cluster_counts():
    items = [
        raw_item(1, NOON, title="같은 제목의 보도자료 기사", domain="b.kr"),
        raw_item(2, NOON - timedelta(minutes=1), title="같은 제목의 보도자료 기사", domain="a.kr"),
    ]
    session = SearchSession("q", collapse_duplicates=True, today=TODAY)
    run(session, [page(items)])
    result = session.result()
    assert [i.domain for i in result.items] == ["a.kr"]
    assert result.clusters[0].count == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query": " "},
        {"query": "q", "max_results": 0},
        {"query": "q", "date_from": date(2026, 11, 10), "date_to": date(2026, 11, 1)},
    ],
)
def test_invalid_arguments(kwargs):
    with pytest.raises(InvalidRequestError):
        SearchSession(**kwargs)
