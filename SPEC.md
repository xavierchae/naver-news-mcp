# naver-news-mcp 기술 Spec

작성일: 2026-11-13 · 상태: 구현 완료 (v0.1.0)

## 1. 목표

네이버 검색 API로 뉴스를 검색하고 기사 본문을 조회하는 MCP 서버를 만든다.
뉴스 조회 로직은 MCP와 무관한 독립 라이브러리로 분리해 다른 프로젝트에서 재사용한다.
MCP 서버는 당분간 로컬(stdio)에서만 운용한다.

## 2. 결정 사항

| 항목 | 결정 |
|---|---|
| 언어 / 최소 버전 | Python 3.11+ |
| 패키지 관리 | uv workspace, 패키지 2개 |
| 기능 범위 | 뉴스 검색 + 네이버 뉴스 페이지 본문 추출 |
| 코어 API 스타일 | async 우선 + sync 클라이언트 함께 제공 |
| 부가 기능 | 기간·언론사 필터(기본 기간: 오늘), 중복 기사 묶음 표시(개별 record 유지), 재시도·rate limit |
| MCP 프레임워크 | 공식 `mcp` SDK 2.x (`MCPServer`, 1.x의 FastMCP가 개명됨) |
| Transport | stdio |
| 인증 정보 | 환경변수 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`(대체 이름 `NaverClientID`, `NaverClientSecret`) + `.env` 지원 |
| 대상 클라이언트 | Claude Code, Claude Desktop, 자체 에이전트/스크립트 |
| 개발 도구 | ruff(린트·포맷), pytest + pytest-asyncio + respx |
| 제외 | 로컬 캐시, 언론사 원문 페이지 범용 추출, 원격(HTTP) 배포, mypy |

## 3. 저장소 구조

```
naver-news-mcp/
├── pyproject.toml              # workspace root, ruff·pytest 설정
├── .env.example
├── README.md
├── packages/
│   ├── naver-news/             # 코어 라이브러리 (MCP 의존성 없음)
│   │   ├── pyproject.toml
│   │   └── src/naver_news/
│   │       ├── __init__.py     # 공개 API export
│   │       ├── client.py       # AsyncNaverNewsClient, NaverNewsClient
│   │       ├── models.py       # pydantic 모델
│   │       ├── search.py       # 요청 생성·응답 파싱 (순수 함수)
│   │       ├── article.py      # 본문 HTML 파싱 (순수 함수)
│   │       ├── filters.py      # 기간·언론사 필터, 중복 제거
│   │       ├── transport.py    # 재시도, rate limit
│   │       └── errors.py
│   └── naver-news-mcp/         # MCP 서버
│       ├── pyproject.toml      # entry point: naver-news-mcp
│       └── src/naver_news_mcp/
│           ├── server.py       # MCPServer 인스턴스, tool 정의
│           ├── config.py       # pydantic-settings (.env 로드)
│           └── __main__.py
└── tests/
    ├── naver_news/
    ├── naver_news_mcp/
    └── fixtures/               # 검색 응답 JSON, 기사 HTML 샘플
```

의존성

- `naver-news`: `httpx`, `pydantic` v2, `selectolax`
- `naver-news-mcp`: `mcp`, `pydantic-settings`, `naver-news`(workspace 의존성)

다른 프로젝트에서는 코어만 설치한다.
`uv add "naver-news @ git+<repo>#subdirectory=packages/naver-news"` 또는 로컬 경로 의존성.

## 4. 코어 라이브러리 `naver-news`

### 4.1 설계 원칙

- 요청 생성과 응답·HTML 파싱은 I/O 없는 순수 함수로 두고, async·sync 클라이언트는 그 위의 얇은 I/O 층으로 만든다. 두 클라이언트의 로직 중복을 없애고 Jupyter처럼 이벤트 루프가 이미 도는 환경에서도 sync 클라이언트가 동작한다.
- 코어는 `.env`를 읽지 않는다. 인증 정보는 생성자 인자로 받고, 편의용 `from_env()`는 `os.environ`만 읽는다. `.env` 로딩은 애플리케이션(MCP 서버)의 책임이다.
- `httpx` 클라이언트를 외부에서 주입할 수 있게 해 테스트와 프록시 설정을 쉽게 한다.

### 4.2 공개 API

```python
from naver_news import AsyncNaverNewsClient, NaverNewsClient

async with AsyncNaverNewsClient(client_id="...", client_secret="...") as client:
    result = await client.search(
        "인공지능",
        max_results=30,
        sort="date",  # "date" | "sim"
        date_from=TODAY,  # 기본값. date 객체 또는 None(기간 제한 없음)
        date_to=None,
        include_domains=["yna.co.kr"],
        exclude_domains=None,
        collapse_duplicates=False,  # 기본: 중복 기사도 개별 record로 모두 반환
        include_press_info=False,  # True면 기사 페이지에서 언론사명·기자를 채움
    )
    article = await client.get_article(result.items[0].link)

client = NaverNewsClient.from_env()  # sync, 동일한 메서드
```

### 4.3 모델

| 모델 | 필드 |
|---|---|
| `NewsItem` | `title`, `description`, `link`, `original_link`, `published_at`(aware datetime), `domain`, `is_naver_news`, `cluster_id`, `is_representative`, `press`·`authors`(`include_press_info` 사용 시에만 채워짐) |
| `NewsCluster` | `cluster_id`, `title`(대표 기사 제목), `count`(기사화 건수), `domains`, `first_published_at`, `last_published_at` |
| `SearchResult` | `query`, `total`(API가 보고한 전체 건수), `items`, `clusters`, `date_from`, `date_to`, `scanned`(훑어본 건수), `has_more`(`max_results`에 걸려 조건에 맞는 기사가 더 있을 수 있음), `truncated`(API 한도로 중단 여부) |
| `Article` | `url`, `title`, `press`, `authors`, `published_at`, `modified_at`, `content` |

`title`·`description`의 `<b>` 태그와 HTML 엔티티는 제거해 반환한다.

### 4.4 검색

- 엔드포인트: `GET https://openapi.naver.com/v1/search/news.json`
- 파라미터: `query`, `display`(1–100), `start`(1–1000), `sort`(`sim`|`date`)
- 헤더: `X-Naver-Client-Id`, `X-Naver-Client-Secret`
- `max_results`를 채울 때까지 내부에서 페이지네이션한다.

### 4.5 기간·언론사 필터

검색 API에는 날짜·언론사 파라미터가 없으므로 클라이언트 측에서 거른다.

- 기간 기본값: `date_from`을 생략하면 오늘(KST 00:00 이후) 발행된 기사만 검색한다. 다른 기간은 날짜를 명시하고, 기간 제한을 없애려면 코어에서는 `date_from=None`, MCP tool에서는 `date_from="all"`을 준다.
- 기간: `sort="date"`로 최신순 페이지네이션하며 `published_at`이 `date_from`보다 오래되면 중단한다. 기간 제한이 있으면 `sort`는 `date`로 강제하므로, 정확도순(`sim`)은 기간 제한을 없앴을 때만 쓸 수 있다.
- 언론사: API가 언론사명을 주지 않으므로 `original_link`의 도메인 기준으로 포함/제외한다. 언론사명은 본문 조회(`Article.press`)에서만 얻는다.
- 한계: API가 `start` ≤ 1000까지만 허용해 질의당 최신 약 1,100건까지만 훑을 수 있다. 그보다 오래된 기간은 도달 불가이며 이때 `truncated=True`로 알린다.

### 4.6 중복 기사 묶음

보도자료가 여러 언론사에 기사화된 경우 기사화 건수와 매체·기자를 파악할 수 있어야 하므로, 중복 기사는 삭제하지 않고 묶음(cluster)으로 표시만 한다.

- 제목을 정규화(공백·문장부호·`[속보]` 같은 말머리 제거)한 뒤 `difflib.SequenceMatcher` 유사도 0.85 이상이면 같은 묶음으로 본다(임계값은 인자로 조정).
- 모든 기사는 개별 record로 `items`에 남고, 각 record에 `cluster_id`와 `is_representative`(묶음에서 가장 먼저 발행된 기사)가 붙는다.
- `clusters`에 묶음별 요약(기사화 건수, 매체 도메인 목록, 최초·최종 발행 시각)을 함께 반환한다.
- `collapse_duplicates=True`를 주면 `items`에 대표 기사만 남긴다. 이때도 `clusters`의 건수와 매체 목록은 유지된다.
- `max_results`는 개별 record 기준으로 센다. 묶음별 건수는 반환된 record 범위 안에서 집계하므로, `has_more=True`이면 `max_results`를 늘려야 전체 기사화 건수가 된다.
- 완전히 같은 URL이 API 응답에 반복된 경우에만 하나로 합친다.

언론사명·기자: 검색 API는 도메인만 주므로, `include_press_info=True`일 때 네이버 입점 기사에 한해 기사 페이지를 읽어 `press`와 `authors`를 채운다(본문은 반환하지 않음). 기사 수만큼 페이지 요청이 추가되어 느려지며, 미입점 기사는 `domain`만 남는다.

### 4.7 본문 추출

- 대상: `n.news.naver.com` 기사 페이지(검색 결과의 `link`가 네이버 뉴스인 경우).
- `selectolax`(lexbor 백엔드)로 제목, 언론사, 기자, 발행·수정 시각, 본문(`#dic_area`)을 추출하고 사진 캡션·광고·기자 구독 영역은 제거한다.
- 연예·스포츠(`m.entertain.naver.com`, `m.sports.naver.com`)는 페이지 구조가 달라 best-effort.
- 네이버 미입점 언론사의 원문 링크는 지원하지 않으며 `ArticleExtractionError`를 낸다.
- 셀렉터는 `article.py` 한 곳에 모아 네이버 마크업 변경 시 수정 범위를 좁힌다.

### 4.8 재시도·rate limit

- 재시도: 429, 5xx, 네트워크 오류에 지수 백오프 + 지터, 기본 3회. 4xx(인증 오류, 잘못된 질의)는 재시도하지 않는다.
- rate limit: 검색 API 기본 초당 10건, 본문 페이지 동시 3건·요청 간 간격을 둔다. 값은 생성자에서 조정 가능.
- 타임아웃: 기본 10초.

### 4.9 예외

`NaverNewsError` ← `AuthenticationError`, `QuotaExceededError`(일 25,000건 초과), `InvalidRequestError`, `ArticleExtractionError`, `TransportError`

## 5. MCP 서버 `naver-news-mcp`

### 5.1 Tools

**`search_news`** — 네이버 뉴스 검색

| 인자 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `query` | str | 필수 | 검색어 |
| `max_results` | int | 10 | 1–100 |
| `sort` | "date" \| "sim" | "date" | 최신순 / 정확도순(`date_from="all"`일 때만) |
| `date_from` | str | "today" | "today" \| YYYY-MM-DD \| "all"(기간 제한 없음), KST 기준 |
| `date_to` | str (YYYY-MM-DD) | 없음 | 발행일 상한, 생략 시 현재까지 |
| `include_domains`, `exclude_domains` | list[str] | 없음 | 언론사 도메인 필터 |
| `collapse_duplicates` | bool | false | true면 묶음별 대표 기사만 반환 |
| `include_press_info` | bool | false | true면 언론사명·기자 정보를 채움 |

반환: `SearchResult` (structured output)

**`get_article`** — 기사 본문 조회

| 인자 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `url` | str | 필수 | `search_news` 결과의 `link` |
| `max_chars` | int | 8000 | 본문 길이 상한(컨텍스트 보호) |

반환: `Article` (structured output)

여러 기사 본문이 필요하면 클라이언트가 `get_article`을 병렬 호출한다(별도 batch tool 없음).

### 5.2 동작

- `AsyncNaverNewsClient`는 첫 tool 호출 때 하나 만들어 서버 수명 동안 공유하고, lifespan 종료 시 닫는다.
- 코어 예외는 원인과 조치가 담긴 tool 오류 메시지로 변환한다(예: 인증 실패 시 환경변수 이름 안내).
- 인증 정보가 없으면 서버는 뜨되 tool 호출 시 설정 방법을 안내하는 오류를 반환한다.
- 로그는 stderr로만 출력한다(stdout은 stdio 프로토콜 전용). 인증 정보는 로그에 남기지 않는다.

### 5.3 설정

| 환경변수 | 필수 | 기본값 |
|---|---|---|
| `NAVER_CLIENT_ID` | 예 | — |
| `NAVER_CLIENT_SECRET` | 예 | — |
| `NAVER_NEWS_TIMEOUT` | 아니오 | 10 |
| `NAVER_NEWS_LOG_LEVEL` | 아니오 | INFO |

인증 정보는 `NaverClientID` / `NaverClientSecret` 이름으로도 읽으며, 두 이름이 모두 있으면 `NAVER_*`가 우선한다. 코어의 `from_env()`도 같다.

우선순위: 프로세스 환경변수 > `.env`. `.env`는 `.gitignore`에 넣고 `.env.example`만 커밋한다.

### 5.4 실행·등록

```bash
# 실행
uv run --directory /path/to/naver-news-mcp naver-news-mcp

# Claude Code
claude mcp add naver-news -e NAVER_CLIENT_ID=... -e NAVER_CLIENT_SECRET=... \
  -- uv run --directory /path/to/naver-news-mcp naver-news-mcp
```

README에 Claude Desktop(`claude_desktop_config.json`) 설정과 자체 스크립트에서 stdio 클라이언트로 띄우는 예시를 함께 싣는다.

## 6. 테스트

- 코어: 저장한 검색 응답 JSON과 기사 HTML fixture로 파싱·필터·중복 제거를 단위 테스트한다. HTTP는 respx로 모킹해 페이지네이션, 재시도, 오류 매핑을 검증한다.
- MCP 서버: SDK의 in-memory 클라이언트(`mcp.client.Client(server)`)로 tool 목록·스키마·호출 결과를 검증한다.
- 실제 API를 부르는 테스트는 `@pytest.mark.live`로 분리하고 기본 실행에서 제외한다.

## 7. 유의 사항

- 네이버 개발자센터에서 애플리케이션을 등록하고 "검색" API 사용을 켜야 한다.
- 본문 추출은 공식 API가 아니라 웹 페이지 파싱이다. 마크업 변경에 깨질 수 있고, 수집한 본문의 재배포·대량 수집은 네이버 약관과 언론사 저작권에 걸릴 수 있으므로 개인·내부 조회 용도와 낮은 요청 빈도를 전제로 한다.

## 8. 구현 순서

1. workspace 골격, ruff·pytest 설정
2. 코어: 모델 → 검색(파싱·페이지네이션) → 재시도·rate limit → 필터·중복 제거 → 본문 추출 → sync 클라이언트
3. MCP 서버: 설정 → tool 2종 → 오류 변환
4. README, 클라이언트별 등록 예시
5. 실제 API 키로 live 테스트 및 Claude Code 연결 확인
