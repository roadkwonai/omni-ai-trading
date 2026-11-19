import longterm_dca as D
import longterm_dca_guide as G
import longterm_dca_view as V


def test_every_setting_has_a_plain_tooltip_with_range_and_default_from_the_code():
    for key in ('on', 'style', 'profile', 'when_by', 'size_by', 'me_when', 'buy_every', 'amount', 'frequency', 'day', 'weekday', 'time', 'pick_on',
                'picks', 'master_picks', 'max_symbols', 'max_per_symbol', 'max_total', 'watchlist', 'bundle'):
        assert G.tip(key), key
    assert f'{D.AMOUNT[0]:,}원' in G.tip('amount') and f"{D.DEFAULT['amount_krw']:,}원" in G.tip('amount')
    assert D.TIMES[0] in G.tip('time') and D.TIMES[1] in G.tip('time')
    low, high = D.LIMITS['max_total']
    assert f'{low:,}' in G.tip('max_total') and f'{high:,}' in G.tip('max_total') and f"{D.DEFAULT['max_total']:,}" in G.tip('max_total')
    assert all(D.BUY_EVERY[k] in G.tip('buy_every') for k in D.BUY_EVERY) and all(v in G.tip('when_by') for v in D.BY.values())
    for key in ('rate', 'pace', 'wait', 'floor', 'mult'):  # the tune inputs explain themselves
        assert G.field_tip(key)


def test_master_table_has_every_master_its_multipliers_and_replay():
    import vibe_longterm as L
    rows = G.master_rows()
    assert len(rows) == len(D.PROFILES)
    marks = next(r for r in rows if r['거장'] == D.PROFILES['marks']['label'])
    assert marks[L.REGIMES['bear'][0]] == '1.3' and marks[L.REGIMES['overheat'][0]] == '0.6' and '하락일 우선' in marks['사는 날']
    assert marks['재현 V1'] == D.PROFILES['marks']['evidence'] and marks['현금 하한(여운봉 계좌)'] == '20%'
    assert '급락일' in next(r for r in rows if r['거장'] == D.PROFILES['williams']['label'])['사는 날']


def test_settings_dictionary_covers_the_saved_keys_and_reads_defaults():
    rows = G.dictionary_rows()
    names = {n for r in rows for n in r['저장 이름'].split(' · ')}
    assert set(D.DEFAULT) - {'pick_from'} <= names | {'style'}
    amount = next(r for r in rows if r['저장 이름'] == 'amount_krw')
    assert amount['기본값'] == f"{D.DEFAULT['amount_krw']:,}원"


def test_guide_button_opens_the_dialog_with_its_parts(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr('accounts.load', lambda *a: {})

    def view(root):
        import longterm_dca_view
        import vibe_longterm as L
        longterm_dca_view.render(root, L.load_settings(root))
    at = AppTest.from_function(view, args=(tmp_path,)).run()
    assert not at.exception
    assert at.toggle(key='dca_on_actual').help and at.number_input(key='dca_amount_actual').help
    at.button(key='dca_guide').click().run()
    assert not at.exception
    labels = [t.label for t in at.tabs]
    assert all(any(p in label for label in labels) for p in ('흐름', '언제·얼마나', '거장', '설정값', '추천', '주의'))


def test_the_master_table_shows_the_grade_for_the_regime():
    rows = G.master_rows('correction')
    marks = next(r for r in rows if r['거장'] == D.PROFILES['marks']['label'])
    assert marks['지금 국면 등급'].startswith(D.profile_grade('marks', 'correction')[0]) and G.master_rows()[0]['지금 국면 등급'] == '—'


def test_size_tooltips_and_the_reservoir_difference_are_explained():
    assert '수량' in G.tip('size') and f"{D.SHARES_N[1]:,}" in G.tip('shares_n')
    assert '적립금' in G.RESERVOIR_DIFF and '운용금' in G.RESERVOIR_DIFF
    names = {n for r in G.dictionary_rows() for n in r['저장 이름'].split(' · ')}
    assert {'size', 'shares_n', 'when_by', 'size_by', 'me_when'} <= names
