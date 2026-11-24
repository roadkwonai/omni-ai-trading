import pytest

import startup_template as S
from longterm_template import PLACEHOLDER

GOOD = '''# 창업 준비 점검 · 2026-11-24

## 1. 지금 상황
가상 사례입니다. 회사를 그만두고 작은 음식점을 생각하고 있습니다.

## 2. 들은 말과 계획
가맹 본부에서 1년이면 투자금을 되찾는다고 들었습니다.

## 3. 확인한 자료
| 주장 | 확인 결과 | 출처 |
| --- | --- | --- |
| 업종 3년 생존율 | 확인됨 | https://tasis.nts.go.kr/ |
| 1년이면 투자금 회수 | 다름 | [정보공개서](https://franchise.ftc.go.kr/) |
| 이 자리의 예상 매출 | 모름 | |

## 4. 돈 나누기
| 항목 | 금액(만 원) |
| --- | --- |
| 생활비 12개월 | 3,000 |
| 비상금 | 1,000 |
| 창업 자금 | 6,000 |
| 합계 | 10,000 |

## 5. 작게 시험하기
주말 공유 주방에서 두 달 동안 먼저 팔아 봅니다.

## 6. 멈출 기준
2027-05-31까지 월 순이익이 100만 원이 안 되면 그만둡니다.

## 7. 아직 모르는 것
권리금을 돌려받을 수 있는지 확인하지 못했습니다.

## 8. 사람의 결정
결정: 보류 (2026-11-24, 가족과 한 번 더 상의)
'''


def test_a_finished_check_passes():
    assert S.check(GOOD) == []


def test_the_blank_form_has_every_section_and_is_not_finished():
    blank = S.blank('2026-11-24')
    assert PLACEHOLDER in blank and all(f'. {s}\n' in blank for s in S.SECTIONS)
    assert any('채우지 않은 자리' in p for p in S.check(blank))


@pytest.mark.parametrize('old,new,word', [
    ('| 업종 3년 생존율 | 확인됨 | https://tasis.nts.go.kr/ |', '| 업종 3년 생존율 | 확인됨 | 지인 |', '출처 링크'),
    ('| 이 자리의 예상 매출 | 모름 | |\n', '', '3줄'),
    ('| 이 자리의 예상 매출 | 모름 | |', '| 이 자리의 예상 매출 | 아마도 | |', '확인 결과'),
    ('| 합계 | 10,000 |', '| 합계 | 9,000 |', '합계'),
    ('| 생활비 12개월 | 3,000 |', '| 생활비 12개월 | 0 |', '생활비'),
    ('| 창업 자금 | 6,000 |', '| 창업 자금 | 모름 |', '숫자'),
    ('2027-05-31까지 월 순이익이 100만 원이 안 되면 그만둡니다.', '장사가 안 되면 그때 가서 생각해 봅니다.', '멈출 기준'),
    ('결정: 보류', '결정: AI 추천', '결정'),
    ('1년이면 투자금을 되찾는다고 들었습니다.', '연락처는 010-1234-5678 입니다.', '개인정보'),
    ('## 7. 아직 모르는 것\n권리금을 돌려받을 수 있는지 확인하지 못했습니다.\n', '', '빠진 절'),
])
def test_each_gap_is_reported(old, new, word):
    assert old in GOOD
    assert any(word in p for p in S.check(GOOD.replace(old, new))), S.check(GOOD.replace(old, new))


def test_a_heard_promise_may_be_quoted():
    assert S.check(GOOD.replace('1년이면 투자금을 되찾는다고 들었습니다.', '"수익을 보장한다"는 말을 들었습니다.')) == []
