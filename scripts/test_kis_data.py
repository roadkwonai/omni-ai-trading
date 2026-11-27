from datetime import date, datetime, timedelta
from types import SimpleNamespace

import kis_data as kis
import pytest


def test_quotes_use_environment_specific_keys_hosts_and_token_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(kis, '_keys', lambda mode: (f'{mode}-key', f'{mode}-secret'))

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == 'POST':
            return 200, {'access_token': kwargs['json']['appkey'] + '-token', 'expires_in': 86400}
        return 200, {'rt_cd': '0', 'output': {'stck_shrn_iscd': '005930', 'stck_prpr': '50000'}}

    monkeypatch.setattr(kis, '_request', request)
    for mode in ('live', 'mock', 'mock'):
        assert kis.quote('005930', mode=mode)['price'] == 50000
    assert [call[1] for call in calls if call[0] == 'POST'] == [host + '/oauth2/tokenP' for host in kis.HOSTS.values()]
    for _, url, args in (call for call in calls if call[0] == 'GET'):
        mode = 'mock' if url.startswith(kis.HOSTS['mock']) else 'live'
        assert args['headers']['authorization'] == f'Bearer {mode}-key-token'
        assert args['params']['FID_COND_MRKT_DIV_CODE'] == 'J'
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in kis.ROOT.glob('*.json'))


def test_redirect_is_not_followed_and_response_text_is_not_exposed(monkeypatch):
    monkeypatch.setattr(kis, '_keys', lambda mode: ('key', 'secret'))
    monkeypatch.setattr(kis, '_token', lambda *a, **kw: 'token')

    def redirect(method, url, **kwargs):
        assert method == 'GET' and kwargs['allow_redirects'] is False
        return SimpleNamespace(status_code=302, json=lambda: {'msg1': 'secret', 'rt_cd': '0'})

    monkeypatch.setattr(kis.requests, 'request', redirect)
    with pytest.raises(kis.KISError, match='^KIS HTTP 302$'):
        kis.quote('005930')


def test_daily_bars_use_raw_close_and_reject_out_of_range(monkeypatch):
    row = {'stck_bsop_date': '20261126', 'stck_oprc': '100', 'stck_hgpr': '120', 'stck_lwpr': '90',
           'stck_clpr': '115', 'acml_vol': '200'}

    def get(kind, code, **params):
        assert kind == 'daily' and params['FID_ORG_ADJ_PRC'] == '1'
        return {'output2': [row]}

    monkeypatch.setattr(kis, '_get', get)
    day = date(2026, 11, 26)
    assert kis.daily_bars('005930', day, day) == [(day, 100, 120, 90, 115, 200)]
    row['stck_bsop_date'] = '20261125'
    with pytest.raises(kis.KISError, match='outside requested dates'):
        kis.daily_bars('005930', day, day)


def test_rate_limit_retries_one_read_with_same_token(monkeypatch):
    requests = []
    pauses = []
    monkeypatch.setattr(kis, '_keys', lambda mode: ('key', 'secret'))
    monkeypatch.setattr(kis, '_token', lambda *a: 'token')
    monkeypatch.setattr(kis.time, 'sleep', pauses.append)

    def refused(method, url, **kw):
        requests.append((method, url, kw['headers']['authorization']))
        return 500, {'msg_cd': 'EGW00201'}

    monkeypatch.setattr(kis, '_request', refused)
    with pytest.raises(kis.KISError, match='EGW00201'):
        kis.quote('005930')
    assert len(requests) == 2 and requests[0] == requests[1] and requests[0][0] == 'GET'
    assert pauses == [1]


def test_completed_session_is_reused_without_broker_calls(monkeypatch):
    day = date(2026, 11, 26)
    opening = datetime(2026, 11, 26, 9, tzinfo=kis.KST)
    calls = []

    class Clock(datetime):
        current = opening + timedelta(days=1)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    def get(kind, code, **params):
        calls.append(kind)
        prices = {'stck_oprc': '100', 'stck_hgpr': '120', 'stck_lwpr': '90'}
        if kind == 'daily':
            return {'output2': [dict(prices, stck_bsop_date=params['FID_INPUT_DATE_2'], stck_clpr='100', acml_vol='50')]}
        cursor = params['FID_INPUT_HOUR_1']
        times = [f'{opening + timedelta(minutes=i):%H%M%S}' for i in range(391)]
        return {'output2': [dict(prices, stck_bsop_date=params['FID_INPUT_DATE_1'], stck_cntg_hour=at, stck_prpr='110', cntg_vol='5')
                            for at in [at for at in times if at <= cursor][-120:]]}

    monkeypatch.setattr(kis, '_get', get)
    monkeypatch.setattr(kis, 'datetime', Clock)
    first = kis.day_bars('005930', day)
    assert len(first['bars']) == 391 and first['prev_close'] == 100
    assert calls == ['minute'] * 4 + ['daily']
    assert kis.day_bars('005930', day) == first
    assert calls == ['minute'] * 4 + ['daily']
    assert kis.day_bars('005930', day)['bars'][0][0].tzinfo is kis.KST
    other = day - timedelta(days=1)
    assert kis.day_bars('005930', other)['bars'][0][0].date() == other
    assert kis.day_bars('005930', day) == first
    assert calls == (['minute'] * 4 + ['daily']) * 2
    Clock.current += timedelta(days=1)
    assert kis.day_bars('005930', day) == first
    assert calls == (['minute'] * 4 + ['daily']) * 3
