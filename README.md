# naver-news-mcp

네이버 검색 API로 뉴스를 검색하고 네이버 뉴스 기사 본문을 조회하는 MCP 서버와, 그 기반이 되는 재사용 가능한 Python 라이브러리입니다.

| 패키지 | 내용 |
|---|---|
| `packages/naver-news` | 코어 라이브러리 `naver_news`. MCP 의존성 없음 |
| `packages/naver-news-mcp` | MCP 서버 (stdio). tool: `search_news`, `get_article` |

설계 상세는 [SPEC.md](SPEC.md)를 참고하세요.

## 준비

1. [네이버 개발자센터](https://developers.naver.com)에서 애플리케이션을 등록하고 **검색** API 사용을 설정합니다.
2. 의존성을 설치하고 인증 정보를 넣습니다.

```bash
uv sync
cp .env.example .env   # NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 입력
```

인증 정보는 다음 순서로 찾습니다.

1. 환경변수 `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET`
2. 환경변수 `NaverClientID` / `NaverClientSecret`
3. 서버 작업 디렉터리의 `.env` (MCP 서버만. 변수 이름은 위와 같음)

셸에 환경변수가 이미 있으면 `.env`는 만들지 않아도 됩니다.

## MCP 서버 등록

아래 예시의 `/path/to/naver-news-mcp`는 이 저장소의 절대 경로로 바꿉니다.

서버 프로세스에 인증 정보가 전달되는 방식은 클라이언트마다 다릅니다.

- Claude Code는 실행한 셸의 환경변수를 서버에 물려주므로, 셸에 변수가 있으면 추가 설정이 필요 없습니다.
- Claude Desktop과 MCP SDK의 stdio 클라이언트는 셸 환경변수를 물려주지 않습니다. 설정의 `env`에 적거나 저장소 루트에 `.env`를 둡니다.

### Claude Code

```bash
claude mcp add naver-news -- uv run --directory /path/to/naver-news-mcp naver-news-mcp
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "naver-news": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/naver-news-mcp", "naver-news-mcp"],
      "env": {
        "NAVER_CLIENT_ID": "...",
        "NAVER_CLIENT_SECRET": "..."
      }
    }
  }
}
```

Claude Desktop이 `uv`를 찾지 못하면 `command`에 `which uv`로 확인한 절대 경로를 넣습니다.

### 자체 스크립트

```python
import asyncio
import os

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

server = StdioServerParameters(
    command="uv",
    args=["run", "--directory", "/path/to/naver-news-mcp", "naver-news-mcp"],
    env=dict(os.environ),  # 생략하면 PATH 등 기본 변수만 전달되어 인증 정보가 빠진다
)


async def main():
    async with Client(server) as client:
        result = await client.call_tool("search_news", {"query": "인공지능"})
        print(result.structured_content)


asyncio.run(main())
```

## Tools

### `search_news`

| 인자 | 기본값 | 설명 |
|---|---|---|
| `query` | 필수 | 검색어 |
| `max_results` | 10 | 반환할 기사 수, 1–100 |
| `sort` | `date` | `date` 최신순, `sim` 정확도순 (`date_from="all"`일 때만 적용) |
| `date_from` | `today` | `today` \| `YYYY-MM-DD` \| `all`(기간 제한 없음), KST 기준 |
| `date_to` | 없음 | 발행일 상한 `YYYY-MM-DD` |
| `include_domains`, `exclude_domains` | 없음 | 언론사 도메인 필터 (예: `yna.co.kr`) |
| `collapse_duplicates` | false | true면 유사 제목 묶음마다 대표 기사만 반환 |
| `include_press_info` | false | true면 네이버 입점 기사의 언론사명·기자를 채움 (느려짐) |

- 기본값은 **오늘 발행된 기사만** 검색합니다.
- 제목이 유사한 기사(같은 보도자료의 기사화 등)는 삭제하지 않고 `cluster_id`로 묶어 표시합니다. `clusters`에 묶음별 기사 수와 언론사 도메인이 요약됩니다.
- 묶음별 건수는 반환된 기사 범위 안에서 집계합니다. `has_more`가 true면 `max_results`를 늘려야 전체 건수가 됩니다.
- 네이버 API는 질의당 최신 약 1,100건까지만 조회할 수 있습니다. 이 한도에 걸리면 `truncated`가 true입니다.

### `get_article`

| 인자 | 기본값 | 설명 |
|---|---|---|
| `url` | 필수 | `search_news` 결과 중 `is_naver_news`가 true인 기사의 `link` |
| `max_chars` | 8000 | 본문 길이 상한 |

네이버에 입점한 언론사 기사(`n.news.naver.com`)만 지원합니다. 연예·스포츠 기사는 페이지 구조가 달라 실패할 수 있습니다.

## 라이브러리로 사용

다른 프로젝트에서는 코어 패키지만 설치합니다.

```bash
uv add "naver-news @ git+<저장소 URL>#subdirectory=packages/naver-news"
# 또는 로컬 경로
uv add --editable /path/to/naver-news-mcp/packages/naver-news
```

```python
from datetime import date

from naver_news import AsyncNaverNewsClient, NaverNewsClient

# 비동기
async with AsyncNaverNewsClient(client_id="...", client_secret="...") as client:
    result = await client.search("인공지능", max_results=50, include_press_info=True)
    for cluster in result.clusters:
        print(cluster.count, cluster.title, cluster.domains)
    article = await client.get_article(result.items[0].link)

# 동기 (환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 또는 NaverClientID / NaverClientSecret 사용)
with NaverNewsClient.from_env() as client:
    result = client.search("인공지능", date_from=date(2026, 11, 1))  # 기간 지정
    result = client.search("인공지능", date_from=None, sort="sim")  # 기간 제한 없음
```

코어 라이브러리는 `.env`를 읽지 않습니다. 인증 정보는 생성자 인자나 환경변수로 전달합니다.

## 개발

```bash
uv run pytest                 # 단위 테스트 (네트워크 사용 안 함)
uv run pytest -m live         # 실제 네이버 API 호출, 인증 정보 필요
uv run ruff check . && uv run ruff format .
```

## 유의 사항

본문 추출은 공식 API가 아니라 네이버 뉴스 웹 페이지를 파싱합니다. 네이버 마크업이 바뀌면 동작하지 않을 수 있고, 수집한 본문의 재배포나 대량 수집은 네이버 약관과 언론사 저작권에 저촉될 수 있습니다. 개인·내부 조회 용도로 낮은 빈도로 사용하세요.
