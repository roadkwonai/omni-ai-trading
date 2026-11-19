"""What the long-term engine leaves in its decision log (user request 2026-11-18: keep every input and reason)."""
import json
from datetime import datetime

import vibe_longterm as L
from test_vibe_longterm import ANSWER, DAY, DownRead, KST, Read, dca_switch, decided, fake_fetch, krx_calendar  # noqa: F401


def rows(root, kind):
    db = L.connect(root)
    db.row_factory = __import__('sqlite3').Row
    return [dict(r) for r in db.execute('SELECT * FROM decisions WHERE kind=? ORDER BY rowid', (kind,))]


def test_the_morning_decision_keeps_module_impacts_levels_and_the_story_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_SETTINGS_HISTORY', str(tmp_path / 'h.sqlite3'))
    monkeypatch.setattr(L, 'story_context', lambda: {'trading_journal': {'days': []}, 'premarket': {'as_of': '2026-11-18T08:20'}})
    answer = json.dumps({'market_view': '조정', 'sources': ['Reuters · 2026-11-17 · 반도체 수요', {'title': 'IonQ 실적', 'url': 'https://x'}],
                         'stories': {k: {'status': 'intact', 'tilt': 0, 'reason': 'ok'} for k in L.SATS}})
    now = datetime.combine(DAY, datetime.min.time(), KST).replace(hour=8, minute=45)
    assert L.decide(tmp_path, now, fetch=fake_fetch, ask=lambda text: answer) == 'decided'
    kinds = [r[0] for r in L.connect(tmp_path).execute('SELECT kind FROM decisions ORDER BY rowid')]
    assert kinds == ['regime', 'macro', 'news', 'story', 'map']
    story = json.loads(rows(tmp_path, 'story')[0]['targets'])
    assert story['source'] == 'llm' and story['inputs']['premarket'] and story['inputs']['trading_journal']
    assert story['inputs']['macro'] and story['sources'][0].startswith('Reuters') and 'IonQ' in story['sources'][1]
    mapped = json.loads(rows(tmp_path, 'map')[0]['indicators'])
    assert set(mapped['impacts']) == set(L.MODULES) - {'bogle_core'} and mapped['levels']
    assert L.parse_story(answer)['sources'] and L.parse_story(ANSWER)['sources'] == []  # optional


def test_the_trade_record_keeps_each_modules_note_and_the_planned_money(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=9, minute=40)
    assert L.trade(tmp_path, Read(), now) == 'planned'
    targets = json.loads(rows(tmp_path, 'plan')[-1]['targets'])
    assert targets['why'] == 'initial_deployment' and isinstance(targets['notes'], list)
    assert targets['planned'] and all(isinstance(v, (int, float)) for v in targets['planned'].values())


def test_the_dca_record_keeps_the_setting_and_the_money_it_saw(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    dca_switch(tmp_path, style='master', profile='yeo')
    assert L.dca(tmp_path, Read(), now) == 'dca_planned'
    row = rows(tmp_path, 'dca')[-1]
    seen = json.loads(row['indicators'])
    assert (seen['style'], seen['profile'], seen['amount_krw']) == ('master', 'yeo', 2_000_000)
    assert 'waiting_before' in seen and 'waiting_after' in seen and 'due' in seen
    assert json.loads(row['targets'])['want']


def test_a_failed_or_missed_step_is_written_to_the_decision_log(tmp_path, monkeypatch):
    start = decided(tmp_path, monkeypatch)
    times = iter([start.replace(hour=h, minute=m) for h, m in ((9, 40), (9, 45), (14, 31), (15, 40), (16, 31))])
    clock = {'now': start}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock['now']
    monkeypatch.setattr(L, 'datetime', Clock)
    monkeypatch.setattr(L, 'time', type('T', (), {'sleep': staticmethod(lambda s: clock.__setitem__('now', next(times)))}))
    L.run(tmp_path, DownRead(), False)
    errors = rows(tmp_path, 'error')
    assert errors and errors[0]['reason'].startswith('trade') and 'error' in errors[0]['reason']
    assert len([r for r in errors if r['reason'].startswith('trade')]) == 1  # once per change, not every retry
    assert any(r['reason'].startswith('trade') for r in rows(tmp_path, 'missed'))


def test_the_regime_record_names_its_data_source_and_fetch_time(tmp_path, monkeypatch):
    decided(tmp_path, monkeypatch)
    seen = json.loads(rows(tmp_path, 'regime')[0]['indicators'])
    assert seen['source'].startswith('Yahoo') and seen['fetched_at'].startswith(DAY.isoformat())
    assert 'source' not in L.read_state(tmp_path)['plan']['indicators']  # the rules' inputs stay as they were


class FillRefused(Read):
    """Quotes answer once per ETF, then NH refuses the fill-time quote for 379810 (rate limit, as 2026-11-18 12:02)."""
    def __init__(self):
        super().__init__()
        self.seen = {}

    def call_tool(self, tool, params):
        code = params['stock_code']
        self.seen[code] = self.seen.get(code, 0) + 1
        if code == '379810' and self.seen[code] > 1:
            return {'status': 'error', 'error': '{"category": "rate_limit"}'}
        return super().call_tool(tool, params)


def test_a_dca_order_that_did_not_fill_is_kept_for_the_next_check_not_the_engine(tmp_path, monkeypatch):
    import virtual_paper_broker
    monkeypatch.setattr(virtual_paper_broker, 'RATE_LIMIT_WAIT', (0, 0))
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    L.save_settings(tmp_path, {'execute': True})
    dca_switch(tmp_path, style='master', profile='dalio')
    assert L.dca(tmp_path, FillRefused(), now) == 'dca_traded'
    state = L.read_state(tmp_path)['dca:actual']
    assert state['retry'].get('NDX', 0) > 0 and '다음 확인 때 다시' in rows(tmp_path, 'dca')[-1]['reason']


def test_the_virtual_book_deposits_and_buys_n_shares_of_each_chosen_etf_and_the_shadows_follow(tmp_path, monkeypatch):
    now = decided(tmp_path, monkeypatch).replace(hour=10)
    L.save_settings(tmp_path, {'execute': True})
    dca_switch(tmp_path, size='shares', shares_n=3, pick_on=True, picks=['NDX', 'SPX'])
    broker = L.broker_for(tmp_path, Read())
    before = broker.cash()
    assert L.dca(tmp_path, Read(), now) == 'dca_traded'
    state = L.read_state(tmp_path)['dca:actual']
    import longterm_dca
    spent = 2 * longterm_dca.share_cost(10_000, 3)
    assert state['contributed_krw'] == __import__('pytest').approx(spent) and state['waiting_krw'] == 0
    held = L.broker_for(tmp_path, Read()).holdings()
    assert held['379810'][0] == held['379800'][0] == 3 and abs(L.broker_for(tmp_path, Read()).cash() - before) < 10  # a few won over
    assert json.loads(rows(tmp_path, 'dca')[-1]['indicators'])['size'] == 'shares'
    assert L.close(tmp_path, Read(), now.replace(hour=15, minute=40), execute=True) == 'closed'
    shadow = L.read_state(tmp_path)['shadow:base']['dca']
    assert shadow['contributed_krw'] == __import__('pytest').approx(spent)
