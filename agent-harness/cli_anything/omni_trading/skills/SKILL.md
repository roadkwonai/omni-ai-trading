---
name: cli-anything-omni-trading
description: Omni AI Trading(국내 주식·ETF 장중 매매와 장기투자)의 기록을 읽고 설정 변경을 계획·적용하는 CLI `omni`의 사용법. 계좌·잔고·매매·판단·리포트 조회, 매매 기준·계좌 한도·장기투자·적립식 설정 확인과 변경 요청에 쓴다. 직접 주문하지 않는다.
---

# omni 사용법

저장소 루트에서 실행한다. 설치 전이면 `PYTHONPATH=agent-harness .venv/bin/python -m cli_anything.omni_trading`을 `omni` 대신 쓴다.
더 자세한 사용법(질문별 명령, 답 읽기, 오류 대처, 답하는 예)은 저장소의 `docs/omni-cli.md`, 모든 명령과 설정 주소는 `docs/omni-cli-reference.md`에 있다. 아래 내용으로 부족하면 필요한 절만 읽는다(참조는 길다: `### omni <명령>`으로 찾는다).
답변은 한국어, 시각은 Asia/Seoul로 쓴다. 계좌번호와 키를 찾거나 출력하지 않는다.

## 찾기와 조회

1. `omni guide --json` 또는 `omni guide <검색어>`로 명령을 찾는다.
2. 모든 명령에 `--json`을 붙일 수 있다. `ok`, `data`, `source`(자료 파일과 나이), `warnings`를 읽는다.
3. 자료 나이를 확인한다. 오래된 값을 현재 시세나 잔고라고 말하지 않는다.

| 알고 싶은 것 | 명령 |
| --- | --- |
| 전체 상태·주의 | `omni status` · `omni status alerts` |
| 잔고·오늘 손익·거래 | `omni status balance` · `realized` · `trades` |
| 모의 보유 | `omni jev positions` |
| 매수가 막힌 이유(많은 순) | `omni jev blocks` |
| 왜 팔았나·샀나 | `omni status trades` → `omni jev events --symbol <코드>` (사유는 `reason`·`trigger`, 다른 날은 `--day`) |
| 실계좌 | `omni live status` · `omni live log live-1` · `omni live fills live-1` |
| 장기투자 | `omni lt today` · `levels` · `nav` · `orders --why --day <날짜>` · 적립식은 `dca-preview <장부>` |
| 서비스 일정·상태 | `omni ops services`(정의 파일 만들기는 `ops service-files`, 등록·시작은 사용자가 직접) |
| 리포트·매매일지 | `omni report list` · `omni report read <종류> <키> --lines 20` |
| 설정 | `omni settings list --find <검색어>`(여러 개면 모두 포함한 것) · `omni settings explain <주소>` |

외부로 요청하거나 기록을 남기는 명령은 가이드 6절 표(명령마다는 참조의 '성격')에 있다. 사용자가 원할 때 한 번만 쓴다.

미리보기(저장 안 함): `omni lt preview lt.weights.NDX=0.25 …`, `omni lt dca-preview actual amount_krw=300000`, `omni account quote live-1 50000 20`.

## 설정 바꾸기

사용자가 바꿔 달라고 한 값만 다룬다. 값이나 이유를 추측하지 않는다.

1. `omni settings explain <주소>`로 범위와 실계좌 영향을 본다.
2. `omni --agent <이름> settings plan <주소>=<값> --reason "<사용자가 말한 이유>"`. 아직 바뀌지 않는다.
3. 전후 값, `level`, `affects`, 실계좌가 어떻게 바뀌는지(`how`), 함께 바뀌는 값(`linked`)을 사용자에게 보여 주고 확인을 받는다.
4. `W2`면 `omni plan apply <계획>`.
5. `H`면 `omni plan approve <계획>`을 실행하고, **사용자가 알림에서 받은 코드를 알려 줄 때까지 기다린다.** 받은 코드로 `omni plan apply <계획> --code <코드>`. 사용자가 "~같은데"처럼 확실하지 않게 말하면 알림을 다시 확인해 달라고 하고 쓰지 않는다(세 번 틀리면 막힌다).
6. 적용 결과의 `changes`와 `takes_effect`를 보고한다.

되돌리기: `omni settings history` → `omni settings revert <번호> --reason "…"` → 다시 적용.

## 요청(매매·중단·시작·발송)

모두 설정과 같은 순서다. 명령은 **계획만** 만들고, `omni plan approve <계획>` 뒤 사용자가 알려 준 코드로 `omni plan apply <계획> --code <코드>`를 해야 실행된다. 매매 계획은 10분 안에 적용해야 하고, 그사이 보유가 바뀌면 거절된다.

| 하려는 일 | 명령 |
| --- | --- |
| 모의 보유 매도·추가 매수(실계좌 미러도 따라 함) | `omni trade paper-sell <코드> --qty N`(또는 `--all`) · `paper-buy <코드> --qty N` |
| 실계좌(Jev 미러) 매도·추가 매수 | `omni trade live-sell <계좌> <코드> --qty N`(또는 `--all`) · `trade live-buy <계좌> <코드> --qty N`(미러 보유 종목만) |
| 대기 요청 보기·취소 | `omni jev requests` · `omni trade cancel <paper 또는 계좌> <코드>` |
| 모의 실행기 중단·재시작·중단 해제 | `omni jev stop` · `jev restart` · `jev unstop` |
| 실주문 켜기·끄기 | `omni live on <계좌>`(확인 문구) · `live off <계좌>` |
| 신규 매수 중단·재개 | `omni live pause`(바로) · `live resume` |
| HALT 걸기·풀기 | `omni live halt` · `live unhalt`(확인 문구) |
| 장기 엔진 중지·해제 | `omni lt stop` · `omni lt unstop` |
| 거래일 시작, 등록된 서비스 시작 | `omni ops day-start` · `omni ops start <서비스>`(주문 서비스는 거래일 08:00~15:00) |
| 백업 가져오기 | `omni backup import <이름>`(확인 문구, 서비스가 모두 꺼져 있을 때) |
| 리포트 다시 게시·발송, 알림 시험 | `omni report publish <종류> <키>` · `report send <종류> <키>`(`report list`의 값) · `ops notify-test` |

아직 적용하지 않은 계획은 `omni plan drop <계획>`으로 버린다. 같은 대상의 새 요청 계획은 앞 계획을 대신한다.

모두 `--reason "<사용자가 말한 이유>"`가 필요하다. 사용자가 수량·종목·계좌를 분명히 말하지 않았으면 먼저 묻는다.

계획에 `phrase`가 있으면 **사용자가 그 문구를 직접 말했을 때만** `omni plan apply <계획> --code <코드> --phrase "<문구>"`로 적용한다. 문구를 대신 채우지 않는다. 위험을 늘리는 요청의 결과는 알림 채널로도 간다.

## 유료 웹 검색

`omni ai search`·`omni ai ask`는 Liner로 글을 보내고 요금이 든다. 사용자가 이번 질문에 그러라고 했을 때만 `--yes`를 붙인다(하루 20회). 계좌 정보나 잔고를 질문에 넣지 않는다. 내 기록으로 답할 질문은 `omni ai local`을 쓴다.

## 지킬 것

- 코드를 추측하거나, `~/.vibe-trading` 파일과 DB를 직접 고치거나, 다른 스크립트로 우회하지 않는다.
- 장중(08:55~15:20) 적용은 사용자가 원할 때만 `--during-market`을 붙인다.
- 직접 주문, 새 종목 매수, 서비스 등록·`--execute` 변경 명령은 없다. 대신 할 수 있다고 말하지 않는다.
- `live pause`는 **모든 실계좌**의 신규 매수를 **바로** 멈춘다(먼저 사용자에게 확인). `lt stop`은 장기투자 실계좌의 오늘 주문을 멈추지 않는다. `live off`와 `live halt`는 **손절 주문까지 멈추므로** 승인 코드가 필요하다.

## 종료 코드

0 성공 · 1 실패 · 2 사용법 · 3 사용자 승인 필요 · 4 거절 · 5 자료 없음·오래됨 · 6 외부 조회 실패
