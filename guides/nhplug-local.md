# 나무증권 로컬 연동

이 설치는 공식 [NH MCP](https://github.com/PLUG-OpenAPI/nhplug-mcp)를
Vibe-Trading의 외부 MCP로 연결합니다. 공식 코드 커밋은
`413a14581bfbfec46b67ad78d8f59e4dd654dd2c`입니다. Vibe의 숫자 근거 검증을 위해
텍스트 JSON과 함께 표준 MCP `structuredContent`를 반환하는 로컬 호환 패치를 적용했습니다.
원본 변경분은 `scripts/nhplug-structured.patch`에 보관했습니다.
Vibe의 grounding에는 NH 현재가 전용 경로를 추가했습니다. 정상 응답 코드,
요청·응답 종목코드 일치, 코스피·코스닥 시장 구분과 양수 가격을 확인한 뒤
KRW 가격 근거로 기록합니다. 범용 MCP 문자열 파싱은 바꾸지 않았습니다.

## 연결 구조

| MCP 서버 | REST 주소 | 역할 |
| --- | --- | --- |
| `nhplug_readonly` | `https://api.nhplug.com:8443` | 실시간 시세·운영 계좌 조회. 주문 비활성 |
| `nhplug_paper` | `https://moapi.nhplug.com:8443` | 모의 계좌 조회·주문·취소 |

두 서버의 토큰은 공식 규약대로 운영 인증 서버에서 발급됩니다.
`scripts/nhplug_mcp.py`가 실행 모드별 주소와 주문 설정을 고정하며,
상속된 주소·주문 환경변수로 운영 주문을 활성화할 수 없습니다.

키는 저장소 루트 `.env`(`.env.example`을 복사해 만듦, 권한 600)에서 `APP_KEY`와 `APP_SECRET`만 파싱합니다.
`agent.json`의 두 서버는 `--env-file <저장소>/.env`로 이 파일을 가리킵니다(다른 스크립트는 `scripts/local_env.py`로 같은 파일을 찾고, `.env`가 없으면 예전 상위 `.envrc`를 씀).
셸 파일을 실행하지 않고 다른 서비스 키도 NH 프로세스에 전달하지 않습니다.
키는 git에 올라가지 않는 `.env`에만 두고 MCP 설정이나 커밋되는 파일에 복사하지 않습니다.

연결 설정은 `~/.vibe-trading/agent.json`, 공식 MCP 설치는
`~/.vibe-trading/integrations/nhplug-mcp`, Vibe용 안내 스킬은
`~/.vibe-trading/skills/user/namuh-paper/SKILL.md`에 있습니다.
계좌번호를 포함하는 로컬 설정은 소유자만 읽을 수 있게 저장합니다.

## Vibe-Trading에서 사용

ChatGPT·Codex OAuth를 연결했고 계정에서 제공되는 `openai-codex/gpt-6.1-terra`를
선택했습니다. 인증·모델 설정은 `~/.vibe-trading/auth/`와
`~/.vibe-trading/.env`에 보관합니다. 웹에서 새 대화를 시작합니다. 예:

> namuh-paper 스킬을 읽고 나무증권 모의계좌 잔고와 삼성전자 현재가를 확인해줘.

모의 주문을 요청할 때는 종목, 수량/금액 상한, 진입·청산 조건, 실행 횟수 또는
종료 시각을 지정하세요. 조건을 지정하기 전에는 상시 매매 전략을 시작하지 않습니다.
실제 매매나 상시 전략은 이 설치 과정에서 실행하지 않았습니다.

모의계좌가 3개 조회되어 국내주식 잔고를 각각 검증한 뒤 첫 번째 계좌를
기본값으로 지정했습니다. 다른 모의계좌로 바꿀 때는 `agent.json`의 paper
서버 `--account`와 사용자 스킬의 계좌번호를 함께 변경합니다.

## API 사용 시 확인한 차이

- 모의 서버의 현재가 API는 `IGW40023`으로 거부됩니다. 시세는
  `mcp_nhplug_readonly_get_stock_price`, 모의 주문은
  `mcp_nhplug_paper_call_api`로 분리합니다.
- 공식 MCP의 `get_stock_balance`는 필수 필드 `aly_qut_cd`가 누락되어
  노출하지 않습니다. `call_api`에 `krstockInquiryBalance`와
  `act_no`, `bnc_bse_cd="5"`, `ltg_aot_dit_cd="9"`, `aet_bse="2"`,
  `qut_dit_cd="UNT"`, `aly_qut_cd="1"`을 전달합니다.
- `call_api`는 URI가 아니라 `operationId`를 받습니다. 매수/매도/취소는
  `krstockOrderCashBuy`, `krstockOrderCashSell`, `krstockOrderCancel`입니다.
  `describe_api`로 현재 번들의 필드를 확인한 뒤 사용합니다.
- 주문 결과의 `mkt_orr_no`는 접수번호입니다. 체결은
  `krstockInquiryDailyOrderExecution`으로 별도 확인해야 합니다.
- 공식 MCP는 조회 응답의 다음 페이지 헤더를 반환하지 않습니다. 큰 계좌의 잔고를
  전체라고 가정하거나 이를 근거로 청산하지 않습니다. 전체 페이지가 필요해지면
  공식 Python SDK의 `paginate()`로 확장해야 합니다.
- 공식 MCP는 프로세스 메모리에 토큰을 보관합니다. Vibe의 호출별 MCP 실행 방식에
  따라 토큰 발급이 반복될 수 있으므로 고빈도 자동매매에는 현재 구성을 사용하지 않습니다.

공식 자료: [API 안내](https://www.nhplug.com/llms.txt),
[국내주식 명세](https://www.nhplug.com/openapi-docs/krstock/openapi.json),
[공식 SDK](https://github.com/PLUG-OpenAPI/nhplug-sdk).

## 오프라인 검증

```bash
.venv/bin/python -m unittest discover -s scripts -p 'test_nhplug_*.py'
node scripts/test_nhplug_structured.mjs
```

이 검증은 증권 API나 주문을 호출하지 않습니다.

공식 MCP를 새로 설치하거나 업데이트한 뒤에는 호환 패치를 다시 적용하고 빌드합니다.
이미 적용된 패치를 중복 적용하지 않습니다.

```bash
git -C ~/.vibe-trading/integrations/nhplug-mcp apply --check "$PWD/scripts/nhplug-structured.patch"
git -C ~/.vibe-trading/integrations/nhplug-mcp apply "$PWD/scripts/nhplug-structured.patch"
npm run build --prefix ~/.vibe-trading/integrations/nhplug-mcp
```
