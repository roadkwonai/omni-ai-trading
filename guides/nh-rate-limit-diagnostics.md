# NH 호출 제한 진단 기록

`~/.vibe-trading/nhplug/api-calls.sqlite3`의 `nh_budget_diagnostics`는 NH 계좌 조회의 요청 시각, 같은 조회 예약·대기, 전체 호출 대기, 관문 통과 시각, 응답 결과와 완료 시각을 기록한다. `nh_send_attempts`는 진단 행의 `rowid`에 연결되며, MCP 클라이언트 호출 직전 시각과 공유(`shared`)·별도(`fresh`) 경로를 시도 순서대로 기록한다. 계좌번호, 인증정보, 요청·응답 본문은 기록하지 않는다. 시간 숫자는 Unix 초이며 표시 시 Asia/Seoul로 변환한다.

```sql
SELECT d.time, d.caller, d.operation, d.tier, d.requested_at,
       d.previous_reservation, d.requested_wait, d.same_wait, d.budget_wait,
       d.admitted_at, d.same_cap_exceeded, d.budget_cap_exceeded, d.outcome,
       s.attempt, s.path, s.sent_at
FROM nh_budget_diagnostics AS d
LEFT JOIN nh_send_attempts AS s ON s.diagnostic_id = d.rowid
WHERE d.time >= '2026-11-27T09:00' AND d.time < '2026-11-27T15:31'
ORDER BY d.time, s.attempt;
```

실패 요청뿐 아니라 근처의 성공 요청도 함께 대조한다. `sent_at`은 로컬 MCP 클라이언트에 전달하기 직전이며 NH 서버 수신 시각은 아니다. 예약 시각은 실제 전송이나 응답 시각이 아니다. 재사용 연결이 실패해 별도 연결로 넘어가면 여러 전송 시도가 같은 진단 행에 연결될 수 있다. 시도가 없는 행은 해당 지점까지 도달하지 못했거나 이전 버전이 기록한 것이다. 진단 기록은 최선 노력 방식이므로 DB 잠금·프로세스 종료 때 누락될 수 있고 30일 보관한다. 실행 중인 프로세스에는 새 코드가 적용되기 전까지 새 상세 기록이 생기지 않는다.
