# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

설계의 기준 문서는 `SPEC.md`다. 동작이나 인터페이스를 바꾸면 `SPEC.md`와 `README.md`를 함께 고친다.

## 프로젝트 개요

네이버 검색 API로 뉴스를 검색하고 네이버 뉴스 페이지에서 본문을 추출하는 MCP 서버. 로컬 stdio로만 운용한다. 주 용도 중 하나는 보도자료가 어느 매체에 몇 건 기사화됐는지 파악하는 것이다.

## 명령

```bash
uv sync                                   # workspace 전체 설치
uv run pytest                             # 테스트 (live 테스트 제외)
uv run pytest tests/naver_news/test_search.py::test_date_range   # 단일 테스트
uv run pytest -m live                     # 실제 네이버 API 호출, 인증 정보 필요
uv run ruff check . && uv run ruff format .
uv run naver-news-mcp                     # MCP 서버 실행 (stdio)
```

인증 정보는 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 환경변수(대체 이름 `NaverClientID`, `NaverClientSecret`) 또는 저장소 루트의 `.env`로 준다. 이 저장소 소유자의 셸에는 `NaverClientID`/`NaverClientSecret`로 설정돼 있어 live 테스트가 바로 돈다. MCP SDK의 stdio 클라이언트는 셸 환경변수를 서버에 물려주지 않으므로 스크립트로 서버를 띄울 때는 `env`를 명시한다. 단위 테스트는 네트워크와 인증 정보 없이 돈다.

## 구조

uv workspace에 패키지 2개. Python 3.11+.

- `packages/naver-news` — 코어 라이브러리. 다른 프로젝트에서 단독으로 재사용하는 것이 목적이다.
- `packages/naver-news-mcp` — 공식 `mcp` SDK 2.x 기반 서버. 2.x에서는 FastMCP가 `mcp.server.mcpserver.MCPServer`로 바뀌었고 `mcp.server.fastmcp`는 없다. 예상한 오류는 `ToolError`로 올려야 메시지가 클라이언트에 전달된다(다른 예외는 메시지가 가려진다). tool은 `search_news`, `get_article` 두 개.

### 지켜야 할 경계

- 코어는 `mcp`, `pydantic-settings`에 의존하지 않는다. 의존성은 `httpx`, `pydantic`, `selectolax`로 유지한다. selectolax는 `selectolax.lexbor`만 쓴다(1.0부터 `selectolax.parser`는 import 시 오류).
- 코어는 `.env`를 읽지 않는다. 인증 정보는 생성자 인자 또는 `from_env()`(`os.environ`만)로 받는다. `.env` 로딩은 MCP 서버의 `config.py`가 맡는다.
- 요청 생성, 응답 파싱, HTML 파싱, 필터, 묶음 판정은 I/O 없는 순수 함수로 둔다. 검색의 페이지네이션·필터 상태는 `SearchSession`(`next_params()` → `feed()` → `result()`)이 갖고, 두 클라이언트가 이를 공유한다. 검색 동작을 바꿀 때는 클라이언트가 아니라 `SearchSession`을 고친다. `AsyncNaverNewsClient`와 `NaverNewsClient`(sync)는 그 위의 얇은 I/O 층이며, sync 쪽이 async를 `asyncio.run`으로 감싸는 구조가 아니다.
- 네이버 뉴스 페이지 셀렉터는 `article.py` 한 곳에만 둔다.
- MCP 서버는 stdout에 로그를 쓰지 않는다(stdio 프로토콜 전용). 로그는 stderr.

### 놓치기 쉬운 동작

- 기간 기본값은 오늘(KST)이다. `date_from`을 생략하면 오늘 기사만 나온다. 기간 제한 해제는 코어 `date_from=None`, MCP tool `date_from="all"`.
- 검색 API에는 날짜·언론사 파라미터가 없다. 최신순으로 페이지네이션하며 클라이언트 측에서 거르고, 기간 제한이 있으면 `sort`는 `date`로 강제된다.
- API가 `start` ≤ 1000까지만 허용해 질의당 최신 약 1,100건까지만 훑을 수 있다. 한도에 걸리면 `truncated=True`.
- 중복 기사는 삭제하지 않는다. 모든 기사를 개별 record로 남기고 `cluster_id`로 묶음만 표시하며, 묶음 요약은 `clusters`로 반환한다. `collapse_duplicates=True`일 때만 대표 기사만 남긴다.
- 언론사명·기자는 검색 API에 없다. `include_press_info=True`일 때 네이버 입점 기사에 한해 기사 페이지에서 읽어 온다. 미입점 기사는 도메인만 있다.
- 본문 추출은 `n.news.naver.com` 기사만 지원한다. 언론사 원문 페이지 범용 추출과 로컬 캐시는 의도적으로 범위에서 뺐다.

## 테스트

- HTTP는 respx로 모킹한다. 검색 응답은 `tests/conftest.py`의 `raw_item()`/`page()`로 만들고, 기사 HTML은 `tests/fixtures/article.html`(실제 마크업을 본뜬 합성본)을 쓴다.
- MCP 서버는 `mcp.client.Client(server.mcp)` in-memory 클라이언트로 검증한다. 서버 테스트는 `.env`의 영향을 받지 않도록 임시 디렉터리로 이동해 실행한다.
- 실제 API를 부르는 테스트는 `@pytest.mark.live`로 표시하고 기본 실행에서 제외한다.
