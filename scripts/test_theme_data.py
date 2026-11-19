"""테마 묶음 구성종목 목록 (theme_data.daily_members): kept for the day only when every list answered."""
import theme_data as T


def test_a_failed_list_is_asked_again_after_the_retry_wait_and_a_full_answer_is_kept(monkeypatch):
    from datetime import datetime, timedelta
    monkeypatch.setattr(T, '_LISTS', {})
    clock = {'now': datetime(2026, 11, 20, 10, 0, tzinfo=T.KST)}
    monkeypatch.setattr(T, 'now', lambda: clock['now'])
    calls = {'toss': 0, 'naver': 0}
    state = {'toss_up': False}

    def toss(group, universe):
        calls['toss'] += 1
        if not state['toss_up']:
            raise OSError('toss down')
        return {'090430': {}, '278470': {}}, 2

    def naver(group, universe):
        calls['naver'] += 1
        return {'090430': {}, '483650': {}}, 4
    monkeypatch.setattr(T, 'toss_members', toss)
    monkeypatch.setattr(T, 'naver_members', naver)
    codes, _ = T.daily_members('화장품·소비', {'090430', '278470', '483650'})
    assert codes == ['090430', '483650']  # what answered, used this round
    state['toss_up'] = True
    clock['now'] += timedelta(minutes=31)
    codes, _ = T.daily_members('화장품·소비', set())
    assert codes == ['090430', '278470', '483650'] and calls == {'toss': 2, 'naver': 2}  # asked again
    T.daily_members('화장품·소비', set())
    assert calls == {'toss': 2, 'naver': 2}  # kept for the day once both answered


def test_a_partial_list_is_retried_at_most_every_30_minutes_and_the_naver_theme_list_is_read_once_a_day(monkeypatch):
    from datetime import datetime, timedelta
    monkeypatch.setattr(T, '_LISTS', {})
    monkeypatch.setattr(T, '_NAVER_THEMES', {})
    clock = {'now': datetime(2026, 11, 20, 10, 0, tzinfo=T.KST)}
    monkeypatch.setattr(T, 'now', lambda: clock['now'])
    calls = {'toss': 0, 'themes': 0, 'members': 0}

    def toss(group, universe):
        calls['toss'] += 1
        raise OSError('toss down')
    import market_context as mc
    monkeypatch.setattr(T, 'toss_members', toss)
    monkeypatch.setattr(mc, 'themes', lambda: calls.__setitem__('themes', calls['themes'] + 1) or [{'no': 1, 'name': '화장품'}, {'no': 2, 'name': '반도체'}])
    monkeypatch.setattr(mc, 'naver', lambda path: calls.__setitem__('members', calls['members'] + 1) or {'stocks': [{'itemCode': '090430', 'fluctuationsRatio': '+6.0'}]})
    for minutes in (0, 5, 10):  # three rounds inside 30 minutes
        clock['now'] = datetime(2026, 11, 20, 10, minutes, tzinfo=T.KST)
        assert T.daily_members('화장품·소비', {'090430'})[0] == ['090430']
    assert calls == {'toss': 1, 'themes': 1, 'members': 1}
    clock['now'] += timedelta(minutes=31)
    T.daily_members('화장품·소비', {'090430'})
    assert calls['toss'] == 2 and calls['themes'] == 1  # the Naver theme numbers are kept for the day
    T.naver_members('반도체', {'090430'})
    assert calls['themes'] == 1


def test_kiwoom_codes_drop_their_market_suffix(monkeypatch):
    import kiwoom_data
    monkeypatch.setattr(kiwoom_data, 'call', lambda *a, **k: [{'stk_cd': '090430_AL', 'flu_rt': '+7.88', 'trde_prica': '65269',
                                                              'dt': '20261119', 'cntr_tm': '153000'}])
    rows, calls = T.kiwoom_quotes(['090430'])
    assert rows == {'090430': {'change_pct': 7.88, 'value_krw': 65269e6, 'as_of': '20261119 153000'}} and calls == 1


def test_an_empty_naver_theme_list_is_not_kept_for_the_day(monkeypatch):
    # r5: an answer with no themes was cached all day, so daily_members counted Naver as answered and never asked again
    import market_context as mc
    monkeypatch.setattr(T, '_NAVER_THEMES', {})
    answers = [[], [{'no': 1, 'name': '화장품'}]]
    monkeypatch.setattr(mc, 'themes', lambda: answers.pop(0))
    import pytest
    with pytest.raises(ValueError):
        T.naver_themes()
    assert T.naver_themes() == [(1, '화장품')]
