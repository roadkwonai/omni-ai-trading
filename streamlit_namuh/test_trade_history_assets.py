import json
from datetime import date

from streamlit.testing.v1 import AppTest


def _single_day():
    from datetime import date
    import os
    from trade_history_assets import assets

    today = date(2026, 11, 26)
    assets(os.environ['TEST_ASSETS_ROOT'], ('live-1',), today, today)


def test_single_day_uses_first_and_last_observations(tmp_path, monkeypatch):
    path = tmp_path / 'live-balance-live-1.jsonl'
    values = [
        {'time': '2026-11-26T09:00:00+09:00', 'total_assets': 1_000_000},
        {'time': '2026-11-26T13:30:00+09:00', 'total_assets': 900_000},
    ]
    path.write_text(''.join(json.dumps(row) + '\n' for row in values))
    monkeypatch.setenv('TEST_ASSETS_ROOT', str(tmp_path))
    app = AppTest.from_function(_single_day).run()
    assert not app.exception
    row = app.dataframe[0].value.iloc[0]
    assert (row['시작 총자산'], row['마지막 총자산'], row['잔고 변동률(%)']) == (1_000_000, 900_000, -10)


def test_combined_account_uses_latest_eligible_observation(tmp_path):
    from trade_history_assets import balance_history, combined_live

    for key, rows in {
        'live-1': [('15:00', 100)],
        'live-2': [('14:59', 200), ('15:03', 250), ('15:06', 300)],
    }.items():
        path = tmp_path / f'live-balance-{key}.jsonl'
        path.write_text(''.join(json.dumps({'time': f'2026-11-26T{stamp}:00+09:00', 'total_assets': total}) + '\n'
                                for stamp, total in rows))
    today = date(2026, 11, 26)
    rows = balance_history(tmp_path, ('live-1', 'live-2'), today, today, intraday=True)
    combined = combined_live(rows)
    assert (combined[-1]['시각'], combined[-1]['총자산']) == ('2026-11-26T15:03:00+09:00', 350)


def test_combined_account_requires_same_day_and_five_minute_freshness():
    from trade_history_assets import combined_live

    def observation(account, timestamp, total):
        return {'계좌': account, '날짜': timestamp[:10], '시각': timestamp, '총자산': total}

    rows = [
        observation('실계좌 1', '2026-11-25T23:59:00+09:00', 10),
        observation('실계좌 2', '2026-11-26T00:00:00+09:00', 20),
        observation('실계좌 1', '2026-11-26T09:00:00+09:00', 100),
        observation('실계좌 2', '2026-11-26T09:05:00+09:00', 200),
        observation('실계좌 2', '2026-11-26T09:06:00+09:00', 300),
        observation('실계좌 1', '2026-11-26T09:06:00+09:00', 150),
    ]
    result = combined_live(rows)
    assert [(row['시각'], row['총자산']) for row in result] == [
        ('2026-11-26T09:05:00+09:00', 300),
        ('2026-11-26T09:06:00+09:00', 450),
    ]


def _all_account_assets():
    from datetime import date
    import os
    from trade_history_assets import assets

    today = date(2026, 11, 26)
    assets(os.environ['TEST_ASSETS_ROOT'], ('paper-shared', 'live-1', 'live-2'), today, today)


def test_all_account_total_requires_three_same_day_observations_within_five_minutes(tmp_path, monkeypatch):
    paper = tmp_path / 'shared-etf-paper'
    paper.mkdir()
    paths = [paper / 'account-snapshots.jsonl', tmp_path / 'live-balance-live-1.jsonl',
             tmp_path / 'live-balance-live-2.jsonl']
    for path, time, total in zip(paths, ('09:00', '09:04', '09:05'), (100, 200, 300)):
        path.write_text(json.dumps({'time': f'2026-11-26T{time}:00+09:00', 'total_assets': total}) + '\n')
    monkeypatch.setenv('TEST_ASSETS_ROOT', str(tmp_path))
    app = AppTest.from_function(_all_account_assets).run()
    assert not app.exception
    table = app.dataframe[0].value.set_index('계좌')
    assert table.loc['전체 합계 (모의·실계좌)', '마지막 총자산'] == 600
    paths[-1].write_text(json.dumps({'time': '2026-11-26T09:06:00+09:00', 'total_assets': 300}) + '\n')
    app.run()
    assert not app.exception
    table = app.dataframe[0].value.set_index('계좌')
    assert str(table.loc['전체 합계 (모의·실계좌)', '마지막 총자산']) == 'nan'


def test_balance_history_uses_korean_day_and_time_for_latest_value(tmp_path):
    from trade_history_assets import balance_history

    path = tmp_path / 'live-balance-live-1.jsonl'
    records = [
        {'time': '2026-11-25T15:30:00+00:00', 'total_assets': 200},
        {'time': '2026-11-26T00:00:00+09:00', 'total_assets': 100},
        {'time': 'invalid', 'total_assets': 999},
    ]
    path.write_text(''.join(json.dumps(row) + '\n' for row in records))
    today = date(2026, 11, 26)
    observations = balance_history(tmp_path, ('live-1',), today, today, intraday=True)
    assert [row['총자산'] for row in observations] == [100, 200]
    assert [row['총자산'] for row in balance_history(tmp_path, ('live-1',), today, today)] == [200]


def _compare_account_assets():
    from datetime import date
    import os
    from trade_history_assets import assets

    today = date(2026, 11, 26)
    assets(os.environ['TEST_ASSETS_ROOT'], ('paper-shared', 'live-1'), today, today)


def test_account_asset_charts_use_independent_scales(tmp_path, monkeypatch):
    paper = tmp_path / 'shared-etf-paper'
    paper.mkdir()
    for path, total in [(paper / 'account-snapshots.jsonl', 5_000_000_000),
                        (tmp_path / 'live-balance-live-1.jsonl', 1_000_000)]:
        path.write_text(json.dumps({'time': '2026-11-26T09:00:00+09:00', 'total_assets': total}) + '\n')
    monkeypatch.setenv('TEST_ASSETS_ROOT', str(tmp_path))
    app = AppTest.from_function(_compare_account_assets).run()
    assert not app.exception
    chart = json.loads(app.get('vega_lite_chart')[0].proto.spec)
    assert chart['facet']['row']['field'] == '계좌'
    assert chart['resolve']['scale']['y'] == 'independent'


def _combined_period():
    from datetime import date
    import os
    from trade_history_assets import assets

    assets(os.environ['TEST_ASSETS_ROOT'], ('live-1', 'live-2'),
           date(2026, 11, 25), date(2026, 11, 26), combine_live=True)


def test_combined_period_uses_each_days_latest_valid_pair(tmp_path, monkeypatch):
    for key, records in {
        'live-1': [('25T15:00:00', 100), ('26T15:00:00', 150)],
        'live-2': [('25T15:01:00', 200), ('26T15:03:00', 250), ('26T15:06:00', 300)],
    }.items():
        path = tmp_path / f'live-balance-{key}.jsonl'
        path.write_text(''.join(json.dumps({'time': f'2026-11-{stamp}+09:00', 'total_assets': total}) + '\n'
                                for stamp, total in records))
    monkeypatch.setenv('TEST_ASSETS_ROOT', str(tmp_path))
    app = AppTest.from_function(_combined_period).run()
    assert not app.exception
    table = app.dataframe[0].value.set_index('계좌')
    assert (table.loc['실계좌 합산', '시작 총자산'], table.loc['실계좌 합산', '마지막 총자산']) == (300, 400)
    assert table.loc['실계좌 2', '마지막 총자산'] == 300


def _holdings_cards():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from trade_history_assets import holdings

    stamp = datetime(2026, 11, 26, 9, 30, tzinfo=ZoneInfo('Asia/Seoul'))
    snapshots = {
        'live-1': {
            'time': stamp,
            'account': {'총자산': 1_000_000, '총평가금액': 400_000, '총매입금액': 380_000},
            'holdings': [{'종목': '삼성전자', '종목코드': '005930', '보유수량': 5,
                          '평균매입가': 76_000, '현재가': 80_000, '평가금액': 400_000,
                          '평가손익': 20_000, '수익률(%)': 5.26}],
        },
        'live-2': {
            'time': stamp,
            'account': {'총자산': 500_000, '총평가금액': 0, '총매입금액': 0},
            'holdings': [],
        },
    }
    holdings(tuple(snapshots), lambda _keys: (snapshots, {}))


def test_holdings_cards_show_current_value_rate_and_allocation():
    app = AppTest.from_function(_holdings_cards).run()
    assert not app.exception
    cards = [element.value for element in app.markdown if 'sc-value' in element.value]
    assert len(cards) == 2
    assert '+20,000원' in cards[0] and '+5.26%' in cards[0] and '#ef4444' in cards[0]
    assert '+0원' in cards[1] and '#64748b' in cards[1]
    assert any('1,000,000원' in element.value for element in app.markdown)
    assert any('400,000원' in caption.value for caption in app.caption)
    assert any('380,000원' in caption.value for caption in app.caption)
    row = app.dataframe[0].value.iloc[0]
    assert (row['종목'], row['평가손익'], row['수익률(%)']) == ('삼성전자', 20_000, 5.26)
    chart = json.loads(app.get('vega_lite_chart')[0].proto.spec)
    assert any(layer.get('mark', {}).get('type') == 'text' for layer in chart['layer'])


def test_zero_start_balance_still_shows_change_and_unknown_rate(tmp_path, monkeypatch):
    path = tmp_path / 'live-balance-live-1.jsonl'
    values = [
        {'time': '2026-11-26T09:00:00+09:00', 'total_assets': 0},
        {'time': '2026-11-26T13:30:00+09:00', 'total_assets': 1000},
    ]
    path.write_text(''.join(json.dumps(row) + '\n' for row in values))
    monkeypatch.setenv('TEST_ASSETS_ROOT', str(tmp_path))
    app = AppTest.from_function(_single_day).run()
    assert not app.exception
    card = next(element.value for element in app.markdown if 'sc-value' in element.value)
    assert '+1,000원 · —' in card and '#ef4444' in card


def test_holdings_numeric_tones_and_tiny_rate():
    from trade_history_assets import _numeric_tone
    from status_cards import signed_percent

    assert _numeric_tone(-0.00001) == 'color:#3b82f6'
    assert _numeric_tone(0) == ''
    assert _numeric_tone(None) == ''
    assert _numeric_tone(20) == 'color:#ef4444'
    assert signed_percent(-0.00001) != '-0.00%'


def test_balance_read_error_is_reported_without_crashing(monkeypatch, tmp_path):
    import trade_history_assets

    monkeypatch.setenv('TEST_ASSETS_ROOT', str(tmp_path))
    def failed(_root, _account):
        raise PermissionError('unavailable')
    monkeypatch.setattr(trade_history_assets, 'balances', failed)
    app = AppTest.from_function(_single_day).run()
    assert not app.exception
    assert any('잔고 기록을 읽지 못했습니다' in message.value for message in app.error)
