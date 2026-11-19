# 일반 NH 모의주문 경로의 장애·재시작·중복 방지

2026-11-06. 적용 대상은 Vibe의 `MCPRemoteTool.execute → MCPServerAdapter.call_tool`을
통과하는 로컬 NH MCP 연동이다. `nhplug_*` 설정 이름뿐 아니라 `nhplug_mcp.py` 런처로도
식별하므로 서버 별칭을 바꾸어도 동일 가드가 적용된다. 실전 주문은 계속 차단한다.

## 변경과 상태 전이

기존 MCP 어댑터는 도구 호출 자체의 자동 재시도를 하지 않았다. 그러나 모델 재호출과
프로세스 재시작을 넘어서 제출을 기억하는 상태는 없었다.

현재는 NH 주문을 전송하기 전에 사용자 runtime의 `nhplug/orders.sqlite3`에 제출 의도를
커밋한다. `BEGIN IMMEDIATE`로 동시 요청을 직렬화한다. 기본 위치는
`~/.vibe-trading/nhplug/orders.sqlite3`이며 기존 `VIBE_TRADING_HOME` 규칙을 따른다.
폴더는 0700, DB 파일은 0600이다. 계좌·전체 입력은 해시로만 저장하며 인증키·토큰은 저장하지 않는다.

1. 전송 전 `uncertain` 기록을 커밋한다. 전송 전후에 프로세스가 종료되어도 기록은 남는다.
2. 양의 정수 주문번호를 받으면 `acknowledged`로 기록한다. 접수는 체결을 뜻하지 않는다.
3. 타임아웃·연결 끊김·MCP 오류·접수번호 누락이면 불명확 상태를 유지한다.
   모델이 가격을 바꾸거나 앱을 재시작해도 같은 계좌의 신규 매수·매도는 차단된다.
4. 모의 주문체결 조회에서 계좌·주문일·주문번호·종목·수량이 일치하고,
   `체결수량 + 취소수량 = 주문수량`, `미체결수량 = 0`이면 `terminal`로 기록한다.
   운영 서버 응답, 실패/미확인 조회 메시지, 빈 목록·다른 주문·부분 체결은 잠금을 풀지 않는다.
5. 기존 주문을 정리할 수 있도록 전량 취소는 허용하되, 취소도 제출 의도와 중복을 기록한다.
   다른 신규 주문이 가능해지려면 원주문의 종료 상태를 조회로 확인해야 한다.

같은 계좌에서 같은 한국 날짜에 같은 입력으로 다시 요청한 주문은 종료 확인 후에도 차단한다.
반복 전략은 주문마다 영구 저장한 고유한 32자리 소문자 16진수 intent ID를
`MCPServerAdapter.call_tool(..., order_intent_id=...)`에 전달할 수 있다.
같은 ID는 주문 입력을 바꿔도 재사용할 수 없고, 기존 주문이 종료로 대조되기 전에는
다른 ID의 신규 주문도 차단한다. ID를 전달하지 않는 기존 호출의 중복 차단은 유지된다.
ID는 MCP 서버로 전달하지 않으며, 호출자는 전송 전에 ID와 주문 의도를 영구 저장해야 한다.
이 기능만으로 자동매매 실행기가 반복 주문을 지원하는 것은 아니다.
미확인 제출은 날짜가 바뀌어도 만료시키지 않는다.

## 검증

증권 API 대신 제어 가능한 가짜 MCP 응답과 실제 SQLite를 사용한다.
자식 프로세스가 주문을 접수한 것으로 기록한 직후 `os._exit(23)`으로 종료되는 경우도 재현한다.
재시작한 어댑터가 다시 전송하지 않는 것을 검증한다.

- 정상 접수 후 동일 주문 재호출·새 wrapper/adapter 생성
- 타임아웃·연결 끊김·가격을 바꾼 재호출·날짜 변경
- 동시에 들어온 두 제출 중 한 건만 전송
- 모호한 접수 응답·업무 거부·손상된 DB의 전송 차단
- 정확한 체결/취소 확인 후 다음 주문 허용
- 다른 계좌·날짜·종목·주문번호·부분 체결·실패 조회로 잠금이 풀리지 않음
- 운영 매수·매도·취소와 미지원 쓰기 도구의 전송 전 차단
- DB에 원문 계좌번호가 기록되지 않음

```bash
.venv/bin/python -m pytest -q agent/tests/test_nhplug_order_guard.py
.venv/bin/python -m pytest -q agent/tests/test_mcp_client_adapter.py agent/tests/test_mcp_stdio_integration.py agent/tests/test_mcp_specs_cache.py agent/tests/test_mcp_neutralization.py agent/tests/test_registry_mcp_integration.py
.venv/bin/python -m unittest discover -s scripts -p 'test_nhplug_*.py'
node scripts/test_nhplug_structured.mjs
```

## 운용과 복구 한계

- 이번 검증에서는 실제 증권 주문이나 실제 증권 서버에 대한 통신 장애를 발생시키지 않았다.
- 알려진 주문번호가 있으면 모의 `krstockInquiryDailyOrderExecution`을 조회해 상태를 대조한다.
  `order_safety.state=acknowledged`는 접수만 뜻한다. `resubmit_allowed=false`이면 재전송하지 않는다.
- 주문번호 자체를 잃은 제출은 자동 매칭·자동 해제하지 않는다. 증권사 주문 내역과 비공개 실행 기록을
  대조하는 명시적 복구가 필요하며, 이 변경은 강제 해제 도구를 제공하지 않는다.
  DB 삭제나 설정 경로 변경으로 잠금을 우회하면 중복 방지가 사라진다.
- 조회 수량이 문자열이거나 알려지지 않은 완료 메시지이면 보수적으로 잠금을 유지한다.
- 지원 주문은 국내 KRX 현금 매수·매도·전량 취소다. 다른 자산/거래소/쓰기 API를 우회 경로로 사용하지 않는다.
- 일반 MCP 경로와 직접 FastMCP를 사용하는 smoke CLI는 서로 다른 저장소를 사용한다.
  다른 앱·직접 HTTP·smoke CLI까지 계좌 전체를 통합 잠그는 장치는 아니므로 동시에 운용하지 않는다.
- 실행 중인 서버는 기존 Python 모듈을 계속 사용할 수 있다. 이 변경은 새 프로세스부터 적용되며,
  검증 과정에서 공유 중인 앱 서버를 강제 재시작하지 않았다.

## 2026-11-06 검증 결과

- 신규 장애·중복 방지 테스트: 28개 통과.
- 일반 MCP 어댑터·stdio·캐시·중립화·등록과 신규 테스트를 합친 검사: 118개 통과.
- NH 스크립트 검사: 14개 통과. 운영 매수·매도·취소 HTTP 전송 거부 검사 통과.
- 변경 코드 Ruff, Python 컴파일, `git diff --check`: 통과.
- 전체 회귀 실행: 21,331 passed, 176 skipped, 11 failed. 외부 소켓 연결은 차단하고 localhost/Unix socket만 허용했다.
  - `test_every_direct_runtime_root_child_is_gitignored`: 새 `/nhplug/` runtime 경로를 `.gitignore`에 추가하여 해결.
  - `test_ou_only_fits_adjacent_observed_pairs`, `test_ou_rejects_sample_with_no_three_adjacent_pairs`,
    `test_quantlib_tool_refuses_insufficient_adjacent_ou_pairs`: 프로젝트에 이미 선언된 선택 의존성
    `statsmodels`를 로컬 테스트 환경에 설치하여 해결. 의존성 선언은 변경하지 않았다.
  - `test_repo_tree_states_the_real_agent_tool_count`의 7개 언어 사례: 병행 작업의 OpenDART·KRX 도구 추가로
    keyless 도구 수가 118→120으로 증가했다. 공통 `guides/capabilities.md` 수치를 맞췄다.
- 후속 검사: 위 실패군과 관련 Quantlib·NH 검사에서 174개 통과, 수치 동기화 후 나머지 README 7개 통과.
  전체 회귀 21,000여 개를 수정 후 처음부터 재실행한 결과와 혼동하지 않는다.
- 원본 로그: `.venv/nh-general-regression.log`, `.venv/nh-failed-recheck.log`.
- 마지막 재검증 시 병행 작업의 정책 검사 4개가 같은 테스트 파일에 추가되어, 현재 파일 기준으로는 32개가 통과했다. 위 28개는 이번 장애·중복 방지 작업의 기본 검사 수다. 병행 변경은 보존했다.

## 공유 앱 적용 후속 확인

2026-11-06 10:21~10:24 한국시간, 공유 앱을 정상 재시작한 뒤 실제 대화의 MCP 호출에서
운영/모의 두 경로의 `NHOrderBlocked`를 확인했다. 전역 live HALT도 켠 상태로 유지한다.
이 기록은 위의 “공유 앱 서버 재시작 미실시”라는 당시 한계를 보완한다.
[재시작 검증 기록](nhplug-app-restart-verification.md)에 범위와 원본 증거 위치를 남겼다.

## 자동 손실 중단과 현재 국내장 확인

활성 설정: 사용자 `nhplug/swing-policy.json`의 `auto_loss_stop=true`, `krx_session_check=true`.
Vibe 일반 NH MCP 신규매수 경로가 매번 직접 조회한 결과만 사용한다. 호출자가 손익·장 상태를 전달해서 승인받을 수 없다.

- 손실 예산 300,000원. 계산은 `max(-당일매매손익,0) + 제비용 + max(-현재평가손익,0)`이다.
- 이 값은 정확한 당일 순손익이 아닌 보수적 위험액이다. 이익으로 손실을 상쇄하지 않고 이월 평가손실도 포함하며, 비용이 이미 차감된 증권사 손익이면 비용이 중복될 수 있다.
- 예산 이상이면 계좌 해시·한국 날짜별 중단 상태를 `nhplug/loss.sqlite3`에 기록한다. 같은 날은 손익 회복·프로세스 재시작으로 자동 해제되지 않는다. 다음 한국 날짜에 다시 평가한다.
- 손익은 모의 `krstockInquiryRealizedPnl` 전체계좌 조회로 확인한다. 필수 필드 누락·인증/통신 오류·저장 오류는 신규매수 차단이다.
- 장 확인은 운영 조회 전용 MCP에서 해당 종목 KRX 시세를 2초 간격으로 두 번 직접 조회한다. 같은 종목, 정규장 상태, 120초 이내 호가, 두 응답 간 호가 시각 증가를 모두 요구한다.
- 주말·허용 시간 밖·휴장/지연개장·단일가·상태 불명·동일 시각의 정지된 시세는 신규매수를 차단한다. 거래가 드문 종목도 보수적으로 차단될 수 있다.
- 이는 현재 KRX 거래 가능 상태 확인이며, 연간 확정 휴장일 목록이나 미래 개장일 예측 기능은 아니다. NH 호가 시각에는 날짜가 없으므로 단일 스냅샷이 아닌 실제 시각 진행을 요구한다.
- 매도·취소는 이 두 추가 검사 때문에 막지 않는다. 기존 중복·미확인 주문 보호는 그대로 적용한다.
- 손실 검사는 신규매수 직전에 실행한다. 별도 상시 감시나 자동 강제청산을 시작하지 않고 기존 미체결 주문을 자동 취소하지 않는다.
- 적용 범위는 `MCPRemoteTool.execute → MCPServerAdapter.call_tool`이다. 별도 SDK/HTTP/직접 FastMCP 클라이언트로 우회한 주문은 보호 대상이 아니다.
