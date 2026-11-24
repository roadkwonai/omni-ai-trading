# omni

Omni AI Trading의 기록을 읽고, 설정을 바꾸고, 사람이 승인한 요청을 남기는 명령입니다. 직접 주문하지 않습니다. CLI-Anything의 방식(HARNESS.md)을 따라 만들었고, 계산과 저장은 저장소의 기존 함수를 그대로 부릅니다.

## 설치

저장소의 가상환경에 넣습니다(기존 의존성 외에 새로 받는 것은 없습니다). 가상환경에 pip이 있으면 `.venv/bin/python -m pip install --no-deps -e agent-harness`도 됩니다.

```bash
uv pip install --python .venv --no-deps -e agent-harness
```

설치하지 않고 쓰려면 저장소 루트에서 실행합니다.

```bash
PYTHONPATH=agent-harness .venv/bin/python -m cli_anything.omni_trading status
```

다른 위치에서 쓰려면 `OMNI_REPO`에 저장소 경로를 적습니다. 인자 없이 터미널에서 실행하면 대화형으로 열립니다.

## 사용법

에이전트용 가이드는 저장소의 `docs/omni-cli.md`, 명령·설정 참조는 `docs/omni-cli-reference.md`입니다. 참조는 명령이나 설정을 바꾼 뒤 다시 만듭니다.

```bash
.venv/bin/python agent-harness/build_reference.py --probe
.venv/bin/python agent-harness/build_reference.py
```

## 테스트

```bash
PYTHONPATH=agent-harness .venv/bin/python -m pytest -q agent-harness/cli_anything/omni_trading/tests
```

임시 HOME에서만 돌고, 끝날 때 실제 설정 파일이 그대로인지 확인합니다. 내용은 [tests/TEST.md](tests/TEST.md)에 있습니다.
