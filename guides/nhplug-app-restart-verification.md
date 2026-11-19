# 공유 앱 재시작 후 NH 주문 가드 검증

검증일: 2026-11-06, 한국시간 10:21~10:24.

## 결과

공유 앱을 정상 종료 후 재시작했고, 실제 앱 대화 경로에서 NH 주문 가드가 전송 전에 요청을 차단하는 것을 확인했다.
실전 주문 차단을 강화하기 위해 전역 live HALT도 켰으며, 검증 후에도 해제하지 않았다.

| 확인 항목 | 결과 |
| --- | --- |
| 실행 주소 | `http://127.0.0.1:8899` |
| 이전 → 새 서버 PID | 65380 → 84682 |
| 종료 방식 | 기존 `/system/shutdown` API → SIGTERM → 정상 종료 확인. 강제 종료 미사용 |
| 새 서버 실행 | 기존 Python·CLI·바인드 주소·포트·runtime 경로 유지 |
| `/health`, `/ready`, `/` | 재시작 후 모두 HTTP 200 |
| 기존 대화 3개 | 보존 확인 |
| LLM 설정 | 기존 공급자·모델 유지 확인 |
| 전역 실전 주문 중단 | `global_halted=true`, 검증 후에도 유지 |
| 실전 매매 러너 | 재시작 전후 0개 |
| NH 서버 모드 | 운영 `readonly`, 모의 `paper` 유지 |

## 안전하게 종료하기 전 확인

실행 attempt 없음, swarm 실행 없음, 예약 실행 비활성, 채널 런타임 중지,
포트폴리오 갱신·재연결 비실행, 실전 러너 및 mandate 없음을 확인했다.
PID·실행 명령·작업 폴더를 확인한 뒤 해당 서버만 종료했다.
세션/주문 DB·설정·인증 파일을 삭제하거나 잠금을 해제하지 않았다.

## 실제 앱 호출 증거

검증 전용 앱 대화 `051b8909b375`를 만들고, `/sessions/{id}/messages`로 요청했다.
기존 LLM → `load_tools` → `MCPRemoteTool.execute` → `MCPServerAdapter.call_tool` 경로를 사용했다.
직접 Python으로 어댑터를 호출한 결과나 모델의 답변만으로 판정하지 않고,
앱이 저장한 `trace.jsonl`의 원본 도구 결과를 파싱해 아래 값을 확인했다.

| 앱 도구 | 의도적으로 잘못된 입력 | 실제 오류 |
| --- | --- | --- |
| `mcp_nhplug_readonly_call_api` | 매수 operationId, 빈 input | `NHOrderBlocked`: `NH live orders and unsupported write tools are blocked` |
| `mcp_nhplug_paper_call_api` | 가짜 계좌, 수량 0, 가격 0 | `NHOrderBlocked`: `Order account must match the configured paper account` |

둘 다 앱 도구 결과에서 `status=error`, `elapsed_ms=0`이었다.
계좌목록을 조회하거나 입력을 정상 주문으로 수정하지 않았고, 증권사에 주문을 전송하지 않았다.
첫 번째 시도는 도구 스키마 조회를 제한한 탓에 모델이 실행을 거절했다.
읽기 전용 도구 로딩을 허용한 후 두 차단 요청을 각각 한 번만 실행했다.

재시작 당시 주문 가드 소스 SHA-256:
`1431b43456564e537b876204e750e022ec6f8a7126a5cb6c2c00711b2e8a9c95`.
검증 직후에도 이 지문이 유지됨을 확인했다.

비공개 증거는 `.venv/shared-app-restart-result.json`(0600),
`~/.vibe-trading/sessions/051b8909b375/trace.jsonl`에 보관한다.
로그·대화 전체를 외부에 공유할 필요는 없다.

## 검증 범위

유효한 주문의 전송·체결이나 증권사 네트워크 장애는 발생시키지 않았다.
이 검증은 실제 앱 프로세스에 가드가 로드되었고, 운영 주문과 잘못된 모의 주문을
원격 전송 전에 차단한다는 것을 증명한다. 중복·재시작 상태 저장은 기존 오프라인 회귀검사로 확인한다.

같은 폴더에서 진행 중인 별도 작업이 추가한 스윙 preflight 검사 1개가 후속 회귀 실행에서 실패했고,
기존 검사 32개는 통과했다. 해당 신규 preflight 구현을 이 재시작 작업에서 덮어쓰거나 완료로 판정하지 않았다.
진행 중인 소스 변경 전체가 이 서버에 반영됐다는 의미로 확장하지 않는다.
