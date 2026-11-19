import json

import market_playbook as M


def test_entry_and_holding_get_their_own_checks_in_a_short_reference():
    # user 2026-11-18: Jev reads the 실전 시황 트레이딩 lens (our own summary, reference only, not a trading rule)
    entry, holding = M.brief('entry'), M.brief('holding')
    assert entry['role'] == holding['role'] and '매매 규칙 아님' in entry['role']
    assert set(entry['checks']) >= set(M.COMMON) and set(holding['checks']) >= set(M.COMMON)
    assert set(M.ENTRY) <= set(entry['checks']) and not set(M.HOLDING) & set(entry['checks'])
    assert set(M.HOLDING) <= set(holding['checks']) and not set(M.ENTRY) & set(holding['checks'])
    for b in (entry, holding):
        assert len(json.dumps(b, ensure_ascii=False)) < 1500  # every Jev request carries it


def test_the_summary_carries_no_numbers_from_the_book():
    # the book's figures belong to its own setups; the lens is the questions, not the numbers
    assert not any(ch.isdigit() for text in M.COMMON + M.ENTRY + M.HOLDING for ch in text)


def test_the_lens_asks_for_a_confirmed_crash_before_an_inverse_and_a_quick_profit_after():
    # user 2026-11-18: inverse only when every view agrees on a crash; in a sideways tape look at new listings and leaders
    import market_playbook as M
    entry, holding = ' '.join(M.brief('entry')['checks']), ' '.join(M.brief('holding')['checks'])
    assert '인버스' in entry and '폭락' in entry and '신규 상장주' in entry
    assert '인버스' in holding and '작은 수익' in holding
