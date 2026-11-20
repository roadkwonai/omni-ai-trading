"""적립식(DCA) for the long-term engine: v3 (user approval 2026-11-18, docs/longterm-dca-v3-plan.md 3·7).

Off unless the user turns it on for a book (the virtual book or a live account set to 장기투자). It buys only (F1): selling,
rebalancing and the big drawdown buys are the book's strategy (the engine's master modules or the 여운봉 저수지 매매법).
Four parts, each decided by the chosen master (자동) or by the user (수동), mixable:
  · 언제: the master checks every trading day and buys at most once per buy_every period, on its own day (each period's
    first check, or the first down day with a maximum wait, 윌리엄스 also a crash day); or my calendar (매일 · 매주 요일 ·
    매월 n일), bought then (계속) or on the first down day after it (하락일 우선, at most MAX_WAIT_DAYS later).
  · 얼마나: the master's small steady amount: a base (a module book: the incoming 적립금 pace x 0.7; a 여운봉 book: the
    book's value x a weekly rate) x the master's regime multiplier (0.5~1.5, a crash day x1.5), plus 1/8 of what slower
    buys left (catch-up), at most 1.5 x the base; a 여운봉 book never spends under the master's cash floor. Or my amount
    (원) or N shares of each chosen ETF.
  · 무엇을: what the book's strategy buys (vibe_longterm.dca_targets), or the ETFs and 관심목록 codes I pick (with limits).
  · 몇 시: the set time, or the first look at or under yesterday's KRX close (늦어도 TIMES[1]).
The contributions a book has not bought yet are its pool (waiting_krw, reserved from the engine). plan() is pure: the
caller (vibe_longterm.dca for the virtual book, longterm_live for a live account) adds the money (virtual deposit) or checks
the real cash, sends the orders and stores the returned state. Replay: scripts/longterm_dca_v3_replay.py (V1).
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from itertools import takewhile

from longterm_watchlist import CODE

REGIMES = ('bull', 'recovery', 'correction', 'bear', 'panic', 'overheat')
_m = lambda *v: dict(zip(REGIMES, v))  # noqa: E731
BASE = ('bogle_core', 'dalio_rebalance', 'buffett_lowturn')
# master: label, its view, regime multipliers, 언제 (cycle | dip), longest wait for a down day (days), cash floor on a 여운봉
# book, crash boost, theme stories, its engine module bundle (the selling side, saved only on request), V1 replay
# (docs/longterm-dca-v3-plan.md 7-2: 여운봉 book from KRX 2021-11, weekly 0.25%: yearly return · max drawdown · share bought in weak regimes)
PROFILES = {
    'dalio': {'label': '레이 달리오', 'title': '균형 · 과열만 조금 덜', 'mult': _m(1, 1, 1, 1, 1, 0.9), 'when': 'cycle', 'wait': 0, 'floor': 0.15,
              'modules': BASE, 'rule': '정한 주기마다 꾸준히 산다. 과열 국면만 0.9배. 비중 조절은 엔진 월 밴드가 한다.',
              'evidence': '15.05% · MDD −17.8% · 약세 국면 몫 56%'},
    'bogle_buffett': {'label': '존 보글 · 워런 버핏', 'title': '시황 보지 않고 꾸준히', 'mult': _m(1, 1, 1, 1, 1, 1), 'when': 'cycle', 'wait': 0,
                      'floor': 0.10, 'modules': ('bogle_core', 'buffett_lowturn'),
                      'rule': '국면과 상관없이 정한 주기마다 같은 속도로 산다. 현금 하한이 가장 낮다.',
                      'evidence': '15.51% · MDD −18.3% · 약세 국면 몫 40%'},
    'marks': {'label': '하워드 막스', 'title': '시계추 · 비쌀 때 덜, 쌀 때 더', 'mult': _m(0.9, 1, 1.2, 1.3, 1.4, 0.6), 'when': 'dip', 'wait': 5,
              'floor': 0.20, 'modules': BASE + ('marks_pendulum', 'yeo_reservoir'),
              'rule': '하락일을 기다려 산다(최대 5일). 조정 1.2·약세 1.3·투매 1.4배, 과열 0.6배.',
              'evidence': '14.43% · MDD −17.3% · 약세 국면 몫 52%'},
    'yeo': {'label': '여운봉', 'title': '저수지 관점 · 하락할수록 조금 더', 'mult': _m(0.8, 1, 1.2, 1.4, 1.5, 0.5), 'when': 'dip', 'wait': 7,
            'floor': 0.20, 'modules': BASE + ('yeo_reservoir',),
            'rule': '하락일을 기다려 산다(최대 7일). 강세 0.8배에서 투매 1.5배까지, 과열 0.5배. 큰 하락 몰아 사기는 전략(여운봉 매매법)이 한다.',
            'evidence': '13.86% · MDD −16.7% · 약세 국면 몫 65% (현금 하한 30%로 재현, 지금 20%)'},
    'lynch': {'label': '피터 린치', 'title': '테마 스토리 반영', 'mult': _m(1, 1, 1, 1, 1, 0.9), 'when': 'cycle', 'wait': 0, 'floor': 0.15,
              'story': True, 'modules': BASE + ('lynch_story',),
              'rule': '정한 주기마다 산다. 테마 스토리가 주의면 그 테마 몫을 절반, 훼손이면 사지 않는다.',
              'evidence': '재현 불가(LLM 스토리 판정) · 달리오와 같은 속도'},
    'williams': {'label': '래리 윌리엄스', 'title': '급락일 우선 (비권장)', 'mult': _m(1, 1, 1.1, 1.1, 1.2, 0.9), 'when': 'dip', 'wait': 5,
                 'floor': 0.20, 'crash': True, 'caution': True, 'modules': ('bogle_core', 'dalio_rebalance', 'williams_trend', 'williams_rebound'),
                 'rule': '하락일을 기다려 산다(최대 5일). 나스닥100 −3%·VIX 25 이상 급락일이면 1.5배.',
                 'evidence': '14.43% · MDD −17.3% · 약세 국면 몫 51% · 급락일이 드물어 막스와 비슷'},
}
AUTO = {'panic': 'yeo', 'bear': 'bogle_buffett', 'correction': 'marks', 'recovery': 'dalio', 'overheat': 'marks', 'bull': 'bogle_buffett'}
STYLES = {'master': '거장 투자', 'manual': '수동 투자'}
BY = {'master': '자동 · 거장', 'me': '수동 · 내가'}
ME_WHENS = {'cycle': '계속(정한 날마다)', 'dip': '하락일 우선(정한 날 뒤 첫 하락일)'}
MAX_WAIT_DAYS = 7  # 하락일 우선 (mine): bought at the latest this many days after the contribution day
BUY_EVERY = {'day': '매일', 'week': '매주', 'month': '매월'}
BUYS_PER_MONTH = {'day': 21.0, 'week': 4.35, 'month': 1.0}
PER_PERIOD = {'daily': 'day', 'weekly': 'week', 'monthly': 'month', 'deposit': 'day'}  # a contribution frequency's buy period
FREQUENCIES = {'monthly': '매월', 'weekly': '매주', 'daily': '매일', 'deposit': '입금할 때'}
# 입금할 때 (user approval 2026-11-20, docs/longterm-dca-deposit-plan.md): a live account's raised 운용금 is the deposit; no
# calendar. deposit_pct of it becomes the 적립식's money (the rest stays the strategy's reservoir), spread over spread_weeks.
DEPOSIT_PCT, SPREAD_WEEKS = (10, 100), (1, 26)
BUYS_PER_WEEK = {'day': 5.0, 'week': 1.0, 'month': 1 / 4.35}
TIME_MODES = {'fixed': '정한 시각', 'auto': '거장 자동(장중 시황)'}
AUTO_CHECK_MIN = 5  # the automatic time looks every 5 minutes (each look reads the quotes)
WEEKDAYS = ('월', '화', '수', '목', '금')
# 내 조정 over the master's numbers: weekly rate of a 여운봉 book's value, the pace of a module book's 적립금, the
# multipliers by regime, the longest wait for a down day and the cash floor (fractions; days)
TUNE = {'rate': (0.001, 0.01), 'pace': (0.3, 1.0), 'mult': (0.5, 1.5), 'wait': (1, 10), 'floor': (0.05, 0.5)}
RATE, PACE = 0.0025, 0.7  # docs/longterm-dca-v3-plan.md 7-4 (approved 2026-11-18)
CRASH_BOOST, CAP, CATCH_SHARE = 1.5, 1.5, 1 / 8
DEFAULT = {'on': False, 'profile': 'dalio', 'when_by': 'me', 'size_by': 'me', 'me_when': 'cycle', 'buy_every': 'week', 'amount_krw': 1_000_000,
           'frequency': 'monthly', 'day': 1, 'weekday': 0, 'time': '10:00',
           # optional limits on what it buys (user 2026-11-18), each off by default. picks: engine ETF keys and 관심목록 codes;
           # pick_from: {engine ETF key: the masters' US picks that led there} (a label, reference only)
           'pick_on': False, 'picks': [], 'pick_from': {}, 'max_symbols_on': False, 'max_symbols': 3,
           'max_per_symbol_on': False, 'max_per_symbol': 100, 'max_total_on': False, 'max_total': 500,
           # 적립 크기 (user request 2026-11-18): amount_krw a time, or N shares of each chosen ETF
           'size': 'amount', 'shares_n': 1, 'tune': {}, 'time_mode': 'fixed', 'deposit_pct': 100, 'spread_weeks': 4,
           # 적립 알림 (user 2026-11-20): with the 적립식 off, still say on its buy days what it would buy (no order)
           'alert': False}
SIZES = {'amount': '금액 기준', 'shares': '종목별 수량 기준'}
SHARES_N = (1, 1_000)
LIMITS = {'max_symbols': (1, 26),  # six engine ETFs + a full 관심목록 (longterm_watchlist.MAX)
          'max_per_symbol': (1, 100_000), 'max_total': (1, 1_000_000)}
AMOUNT = (10_000, 1_000_000_000)
TIMES = ('09:40', '14:20')  # inside the engine's order window
RESERVOIR = 'RES'
SHORT_OK = 0.01  # fees paid on spent pool money leave the cash a hair under the pool count + the contribution


def profile_grade(key, regime):
    """(grade, one-line reason) of a 적립식 master in a regime, by the same scale as the engine modules (vibe_longterm.GRADES):
    the regime's 시황 자동 master is 강력 추천, a caution profile 비권장, one whose module bundle is all in the regime's
    recommended set 추천, else 선택."""
    from vibe_longterm import REGIMES as LR, recommended_set  # here: vibe_longterm imports this module
    label = LR[regime][0] if regime in LR else '국면 미정'
    if AUTO.get(regime) == key:
        return '강력 추천', f'{label} 국면에 시황 자동이 고르는 거장'
    if PROFILES[key].get('caution'):
        return '비권장', '회전·세금이 크고 재현에서 열위'
    if set(PROFILES[key]['modules']) <= set(recommended_set(regime)):
        return '추천', f'파는 쪽 모듈 묶음이 {label} 국면 추천 조합 안에 있음'
    return '선택', f'파는 쪽 모듈 일부가 {label} 국면 추천 조합 밖'


def clean_tune(tune):
    """{field: value} kept to TUNE's ranges; unknown fields and regimes are dropped."""
    tune = tune if isinstance(tune, dict) else {}
    num = lambda v: type(v) in (int, float) and v == v  # noqa: E731 - finite numbers only (NaN fails v == v)
    fit = lambda v, lo, hi: round(min(hi, max(lo, float(v))), 4)  # noqa: E731
    out = {k: (int(fit(tune[k], *TUNE[k])) if k == 'wait' else fit(tune[k], *TUNE[k])) for k in ('rate', 'pace', 'wait', 'floor') if num(tune.get(k))}
    mult = {r: fit(v, *TUNE['mult']) for r, v in (tune.get('mult') or {}).items() if r in REGIMES and num(v)} if isinstance(tune.get('mult'), dict) else {}
    return out | ({'mult': mult} if mult else {})


def clean(cfg):
    """A complete, in-range setting from whatever was saved or typed. A setting saved before v3 (2026-11-18) is read as
    the v3 way closest to what it did: 정한 날 + 전액 바로 (달리오·보글·린치, 수동 정기, v2 바로) stays mine, a waiting
    or 거장 판단 way (막스·여운봉·윌리엄스, 수동 하락할 때만·혼합 → my down-day calendar) moves to the master, v2 꾸준 to 여운봉."""
    cfg = cfg if isinstance(cfg, dict) else {}
    out = dict(DEFAULT)
    out['on'] = cfg.get('on') is True
    out['profile'] = cfg.get('profile') if cfg.get('profile') in PROFILES else 'dalio'
    tune = cfg.get('tune') if isinstance(cfg.get('tune'), dict) else {}
    manual = cfg.get('style') == 'manual' or ('style' not in cfg and 'profile' not in cfg and cfg.get('mode') in ('regular', 'dip', 'yeo'))
    if 'when_by' in cfg or 'size_by' in cfg:
        out['when_by'] = cfg.get('when_by') if cfg.get('when_by') in BY else 'me'
        out['size_by'] = cfg.get('size_by') if cfg.get('size_by') in BY else 'me'
        out['me_when'] = cfg.get('me_when') if cfg.get('me_when') in ME_WHENS else 'cycle'
    elif cfg.get('when') == 'steady':
        out.update(profile='yeo', when_by='master', size_by='master')
        tune = {'rate': cfg.get('steady_rate'), 'floor': cfg.get('steady_floor')}
    elif manual:
        out['me_when'] = 'dip' if cfg.get('mode') in ('dip', 'yeo') else 'cycle'
    elif cfg.get('timing') == 'master' or out['profile'] in ('marks', 'yeo', 'williams'):
        out.update(when_by='master', size_by='master')
    out['style'] = 'manual' if manual else 'master'
    if manual:  # 수동 투자: every part is mine (the master's numbers are not used)
        out.update(when_by='me', size_by='me')
    out['buy_every'] = cfg.get('buy_every') if cfg.get('buy_every') in BUY_EVERY else 'week'
    out['frequency'] = cfg.get('frequency') if cfg.get('frequency') in FREQUENCIES else 'monthly'
    out['size'] = cfg.get('size') if cfg.get('size') in SIZES else 'amount'
    if out['size'] == 'shares' and 'size_by' not in cfg:  # an old setting's N shares were its own size
        out['size_by'] = 'me'
    # user 2026-11-19: with 얼마나 · 거장 the master sizes a shares book too (its amount rule, bought in whole shares); my N
    # shares are the size only when 얼마나 is mine
    clamp = lambda v, lo, hi, default: min(hi, max(lo, int(v))) if type(v) in (int, float) else default  # noqa: E731
    out['amount_krw'] = clamp(cfg.get('amount_krw'), *AMOUNT, DEFAULT['amount_krw'])
    out['day'] = clamp(cfg.get('day'), 1, 28, DEFAULT['day'])
    out['weekday'] = clamp(cfg.get('weekday'), 0, 4, DEFAULT['weekday'])
    time = cfg.get('time') if isinstance(cfg.get('time'), str) and re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', cfg.get('time') or '') else DEFAULT['time']
    out['time'] = min(TIMES[1], max(TIMES[0], time))
    from vibe_longterm import ASSETS, CODE_KEY  # here: vibe_longterm imports this module
    picks = cfg.get('picks') if isinstance(cfg.get('picks'), list) else []
    ok = lambda k: k in ASSETS or (isinstance(k, str) and CODE.fullmatch(k) and k not in CODE_KEY)  # noqa: E731 - an engine ETF goes by its key
    out['picks'] = [k for i, k in enumerate(picks) if ok(k) and k not in picks[:i]]
    came = cfg.get('pick_from') if isinstance(cfg.get('pick_from'), dict) else {}
    out['pick_from'] = {k: v[:120] for k, v in came.items() if k in ASSETS and k in out['picks'] and isinstance(v, str) and v}
    for key in ('pick_on', 'max_symbols_on', 'max_per_symbol_on', 'max_total_on', 'alert'):
        out[key] = cfg.get(key) is True
    for key, (lo, hi) in LIMITS.items():
        out[key] = clamp(cfg.get(key), lo, hi, DEFAULT[key])
    out['shares_n'] = clamp(cfg.get('shares_n'), *SHARES_N, DEFAULT['shares_n'])
    out['deposit_pct'] = clamp(cfg.get('deposit_pct'), *DEPOSIT_PCT, DEFAULT['deposit_pct'])
    out['spread_weeks'] = clamp(cfg.get('spread_weeks'), *SPREAD_WEEKS, DEFAULT['spread_weeks'])
    if out['frequency'] == 'deposit' and out['size'] == 'shares' and out['size_by'] == 'me':  # my N shares need a calendar
        out['size_by'] = 'master'
    out['tune'] = clean_tune(tune)
    out['time_mode'] = cfg.get('time_mode') if cfg.get('time_mode') in TIME_MODES else 'fixed'
    return out


def rule(cfg, regime=None, profile=None):
    """The master rule a book runs: its profile (or, for the 시황 자동 shadow, the regime's master) with this book's 내 조정
    laid over it (not for a profile override). plan(), the views and the chart read this one merge."""
    cfg = clean(cfg)
    key = profile_for(cfg, regime, profile)
    base = PROFILES[key]
    out = {'key': key, 'label': base['label'], 'title': base['title'], 'rule': base['rule'], 'mult': dict(base['mult']), 'when': base['when'],
           'wait': base['wait'], 'floor': base['floor'], 'crash': bool(base.get('crash')), 'story': bool(base.get('story')), 'rate': RATE,
           'pace': PACE, 'modules': tuple(base['modules']), 'caution': bool(base.get('caution')), 'evidence': base['evidence'], 'tuned': []}
    if profile is None:
        for field, value in cfg['tune'].items():
            out[field] = {**out[field], **value} if isinstance(value, dict) else value
            out['tuned'].append(field)
    return out


def profile_for(cfg, regime, override=None):
    """The PROFILES key a day runs: the book's master, or (a shadow's 'auto') the master the confirmed regime calls for."""
    key = override or cfg['profile']
    return AUTO.get(regime, 'bogle_buffett') if key == 'auto' else key


def tune_text(tune):
    """'주 0.30% · 막스 배수 조정 1.3배 · 현금 하한 25%' ('' without any)."""
    from vibe_longterm import REGIMES as LR
    name = lambda r: LR[r][0] if r in LR else r  # noqa: E731
    parts = [f"주 {tune['rate']:.2%}"] if 'rate' in tune else []
    parts += [f"적립금 속도 ×{tune['pace']:g}"] if 'pace' in tune else []
    parts += [f"{name(r)} {v:g}배" for r, v in (tune.get('mult') or {}).items()]
    parts += [f"하락일 최대 {tune['wait']}일"] if 'wait' in tune else []
    parts += [f"현금 하한 {tune['floor']:.0%}"] if 'floor' in tune else []
    return ' · '.join(parts)


def base_amount(cfg, p, every, value=None, yeo=False, state=None):
    """KRW one buy of the master's size starts from: the incoming 적립금 pace x p['pace'], or on a 여운봉 book the larger of that
    and the book's value x the weekly rate, converted to one buy at frequency `every` (day / week / month). 입금할 때: the
    deposit spread over spread_weeks (take_deposit), never the book's value (that is the reservoir, not a deposit)."""
    if cfg['frequency'] == 'deposit':
        return float((state or {}).get('deposit_base') or 0.0)
    inflow = cfg['amount_krw'] * BUYS_PER_MONTH[PER_PERIOD[cfg['frequency']]] * p['pace'] / BUYS_PER_MONTH[every]
    if yeo:  # the larger, so a small account still spends its 적립금 (50만 원 x 0.25% is 1,250원 a week)
        return max((value or 0) * p['rate'] * BUYS_PER_MONTH['week'] / BUYS_PER_MONTH[every], inflow)
    return inflow


def multiplier(p, regime, ind):
    """(the master's multiplier today, crash day?)."""
    crash = p['crash'] and ((ind or {}).get('day_ret') or 0) <= -0.03 and ((ind or {}).get('vix') or 0) >= 25
    return p['mult'].get(regime, 1.0) * (CRASH_BOOST if crash else 1.0), crash


def buy_period(cfg, when):
    """The buy_every period a date falls in: the master buys at most once in it."""
    if cfg['buy_every'] == 'day':
        return when.strftime('%Y-%m-%d')
    return period({'frequency': 'weekly' if cfg['buy_every'] == 'week' else 'monthly'}, when)


def master_day(cfg, p, state, now, ind, want, prices):
    """The master buys today: not bought yet this buy_every period, and its day has come (a cycle master: the first check;
    a dip master: a down day, a crash day or the longest wait since the last buy)."""
    last = state.get('last_buy')
    if last and buy_period(cfg, date.fromisoformat(last)) == buy_period(cfg, now):
        return False
    if p['when'] == 'cycle':
        return True
    since = date.fromisoformat(state.get('since') or now.date().isoformat())
    return down_day(ind, want, prices) or multiplier(p, None, ind)[1] or (now.date() - since).days >= p['wait']


def quantity_on_signal(cfg):
    """My N shares come in on the master's buy days (not a calendar)."""
    return cfg['size'] == 'shares' and cfg['size_by'] == 'me' and cfg['when_by'] == 'master'


def due_today(cfg, state, now, ind, regime, want=None, prices=None):
    """A contribution comes in now: on the calendar (due), or (my N shares on the master's days) on the master's buy day."""
    if not quantity_on_signal(cfg):
        return due(cfg, state, now)
    if not cfg['on'] or now.strftime('%H:%M') < start_time(cfg):
        return False
    return master_day(cfg, rule(cfg, regime), state, now, ind, want or {}, prices)


def deposit_period(cfg, when):
    """The period a (virtual) deposit is claimed once in: the contribution period, or the buy_every period on the master's buy days."""
    return buy_period(cfg, when) if quantity_on_signal(cfg) else period(cfg, when)


def buy_weights(cfg, want, p=None, stories=None):
    """The weights a contribution buys: universe() (the strategy's or the chosen ETFs), then 린치's story cut."""
    want = universe(cfg, want)
    return story_weights(want, stories) if stories and (p or rule(cfg))['story'] else want


def story_weights(want, stories):
    """린치: a theme whose story is 주의 gets half its share, 훼손 none (the other ETFs as they are)."""
    from vibe_longterm import SATS
    factor = {'watch': 0.5, 'broken': 0.0}
    out = {k: v * factor.get(((stories or {}).get(k) or {}).get('status'), 1.0) if k in SATS else v for k, v in want.items()}
    return {k: v for k, v in out.items() if v > 0}


def plan(cfg, state, now, ind, regime, want, values, cash, arrived=None, profile=None, shares=None, prices=None, yeo=False,
         stories=None, force=False):
    """(orders {asset key: KRW to buy}, new state, Korean notes) for one book, once a day at or after the set time (a
    contribution short of money stays due for the next trading day, so a book is never re-checked every minute).
    ind: the engine's indicators (day_ret, vix, krx_close); regime: the confirmed regime; want: what the book's strategy
    buys (vibe_longterm.dca_targets); values: KRW per held key; cash: money the book can spend now (a virtual deposit
    already added). arrived: KRW that came in now in place of the calendar (a shadow mirroring the virtual book).
    profile: run another master ('auto' for the regime shadow: master 언제 and 얼마나). yeo: a 여운봉 저수지 매매법 book (the
    master's size reads its value and keeps its cash floor). stories: the engine's theme stories (린치). force: buy today
    whatever the day (the views' example). shares / prices feed the share limits and the carry under one share. Never sells."""
    from vibe_longterm import FEE, REGIMES as LR
    cfg = clean(cfg)
    state = {'waiting_krw': 0.0, 'contributed_krw': 0.0, 'carry': {}, 'retry': {}, 'catch_krw': 0.0, **(state or {})}
    for old in ('tp_armed', 'steps_used', 'last_regime', 'steady_week', 'waiting_since'):  # pre-v3 state
        state.pop(old, None)
    day = now.date().isoformat()
    if not cfg['on'] or now.strftime('%H:%M') < start_time(cfg) or state.get('last_check') == day:
        return {}, state, []
    state['last_check'] = day
    p = rule(cfg, regime, profile)
    by_master = {'when': cfg['when_by'] == 'master' or profile is not None, 'size': cfg['size_by'] == 'master' or profile is not None}
    orders, notes = {}, []
    add = lambda part: [orders.__setitem__(k, orders.get(k, 0) + v) for k, v in part.items()]  # noqa: E731
    want = buy_weights(cfg, want, p, stories)
    from vibe_longterm import asset_name as name  # engine ETFs, 여운봉 ETFs (0261H0 …) and 관심목록 codes
    seen = f"{p['label']} · {LR.get(regime, (regime or '국면 모름',))[0]}"

    def join_carry():  # carried parts join a buy
        dropped = {k: v for k, v in state['carry'].items() if k not in want}
        add({k: v for k, v in state['carry'].items() if k in want})
        state['carry'] = {}
        if dropped:
            notes.append(f"적립 대상에서 빠진 종목의 이월 {sum(dropped.values()):,.0f}원 해제(현금으로 남음)")
    if state['retry']:  # what an earlier order did not buy: bought first, or back to the cash when the strategy no longer buys it
        again = {k: v for k, v in state['retry'].items() if k in want}
        add(again)
        if gone := sum(v for k, v in state['retry'].items() if k not in want):  # never into an ETF the strategy does not manage
            notes.append(f'계좌 전략이 사지 않는 종목의 미체결분 {gone:,.0f}원은 현금(저수지)으로 돌림')
        if again:
            notes.append('지난번 체결되지 않은 적립 다시 삼: ' + ', '.join(f'{name(k)} {v:,.0f}원' for k, v in again.items()))
        state['retry'] = {}
    free = cash - reserved(state)
    quantity = cfg['size'] == 'shares' and not by_master['size']
    # the contribution: on my calendar (or a shadow's mirror); my N shares on the master's days come in on its buy day
    if arrived is not None:
        due_now = arrived > 0
    elif quantity and by_master['when']:
        due_now = False
    else:
        due_now = due(cfg, state, now)
    if due_now and not want:
        notes.append('적립할 종목이 없어(계좌 전략·적립 종목 선택·종목 수 제한) 이번 적립을 건너뜀 · 적립으로 기록하지 않음')
        due_now = False
    if due_now and not quantity:
        amount = cfg['amount_krw'] if arrived is None else arrived
        if free < amount * (1 - SHORT_OK):
            notes.append(f"적립 {amount:,}원 예정이지만 여유 자금이 {max(free, 0):,.0f}원뿐이라 건너뜀(다음 거래일에 다시 시도)")
            due_now = False
        else:
            state['waiting_krw'] += amount
            state['contributed_krw'] += amount
            state['last_contribution'] = day
            state.setdefault('since', day)  # my down-day wait runs from the oldest contribution still waiting
            notes.append(f"적립 {amount:,}원 들어옴 · 적립식 몫 {state['waiting_krw']:,.0f}원")
    # the wait for a down day starts with the first check of a buy period the master has not bought in (not at its last
    # buy, so a new week or month never starts with the wait already over), or the day my N shares fell due
    if by_master['when'] and not (state.get('last_buy') and buy_period(cfg, date.fromisoformat(state['last_buy'])) == buy_period(cfg, now)):
        state.setdefault('since', day)
    elif quantity and due_now:
        state.setdefault('since', day)
    waited = (now.date() - date.fromisoformat(state['since'])).days if state.get('since') else 0
    dip_now = lambda: down_day(ind, want, prices) or waited >= MAX_WAIT_DAYS  # noqa: E731
    # 언제
    if force:
        buy = bool(want)
    elif by_master['when']:
        buy = bool(want) and master_day(cfg, p, state, now, ind, want, prices)
    elif cfg['frequency'] == 'deposit':  # my day on a deposit: whenever deposit money waits (계속) or its first down day
        buy = state['waiting_krw'] >= 1_000 and (cfg['me_when'] == 'cycle' or (bool(state.get('since')) and dip_now()))
    elif quantity:
        buy = due_now and (cfg['me_when'] == 'cycle' or dip_now())
    elif cfg['me_when'] == 'cycle':
        buy = due_now
    else:  # once per contribution: a buy clears the wait, the next contribution starts it again
        buy = state['waiting_krw'] >= 1_000 and bool(state.get('since')) and dip_now()
    if not buy:
        if by_master['when'] and want and (state['waiting_krw'] >= 1_000 or yeo):
            notes.append(f"{seen} · 오늘은 사지 않음" + (f"(마지막 매수 {state['last_buy']})" if state.get('last_buy') else ''))
    # 얼마나
    spend, shares_buy = 0.0, False
    if buy and quantity:
        missing = [k for k in want if not (prices or {}).get(k)]
        amount = contribution(cfg, want, prices)
        if missing:
            notes.append(f"시세가 없는 종목({', '.join(map(name, missing))})이 있어 이번 적립을 건너뜀(다음 거래일에 다시 시도)")
        elif free < amount * (1 - SHORT_OK):
            notes.append(f"종목별 {cfg['shares_n']}주 적립 {amount:,.0f}원 예정이지만 여유 자금이 {max(free, 0):,.0f}원뿐이라 건너뜀(다음 거래일에 다시 시도)")
        else:
            add({k: share_cost(prices[k], cfg['shares_n']) for k in want})
            state['contributed_krw'] += amount
            state['last_contribution'] = day
            shares_buy = True
            notes.append((f"{seen}: 오늘 매수 · " if by_master['when'] else '') + f"종목별 수량 기준: {', '.join(map(name, want))} "
                         f"{cfg['shares_n']}주씩 {amount:,.0f}원")
    elif buy and by_master['size']:
        every = cfg['buy_every'] if by_master['when'] else PER_PERIOD[cfg['frequency']]
        value = cash + sum((values or {}).values())
        base = base_amount(cfg, p, every, value, yeo, state)
        mult, crash = multiplier(p, regime, ind)
        today = base * mult
        deposit = cfg['frequency'] == 'deposit'
        # 덜 산 몫, caught up an eighth a buy: a module book's pool beyond today's buy and one contribution (what the 적립금
        # pace above the base left waiting, so no 적립금 waits forever), a 여운봉 book what its buys fell short of the base
        behind = 0.0 if deposit else state['catch_krw'] if yeo else max(0.0, state['waiting_krw'] - today - cfg['amount_krw'])
        extra = behind * CATCH_SHARE if today < CAP * base else 0.0
        room = (cash - (reserved(state) - state['waiting_krw']) - p['floor'] * value) if yeo else min(state['waiting_krw'], free + state['waiting_krw'])
        if deposit:  # only the deposit's money, and still above a 여운봉 book's cash floor
            room = min(room, state['waiting_krw'])
        spend = max(0.0, min(today + extra, CAP * base, room))
        if yeo and not deposit and spend >= 1_000:  # only a real buy moves it (a day the cash floor blocks is not a buy)
            state['catch_krw'] = max(0.0, behind - max(0.0, spend - today) + max(0.0, base - today))
        notes.append(f"{seen}: 기본 {base:,.0f}원 × {mult:g}배" + (' (급락일)' if crash else '') + (f" + 따라잡기 {extra:,.0f}원" if extra >= 1 else '')
                     + f" → {spend:,.0f}원" + (f" · 현금 하한 {p['floor']:.0%}" if yeo else f" · 적립식 몫 {state['waiting_krw']:,.0f}원 안에서"))
    elif buy:
        spend = min(cfg['amount_krw'], state['waiting_krw']) if by_master['when'] else state['waiting_krw']
        notes.append((f"{seen}: 오늘 매수 · 내 금액 " if by_master['when'] else f"{ME_WHENS[cfg['me_when']]}: ") + f"{spend:,.0f}원")
    if spend >= 1_000:
        add({k: spend * w / sum(want.values()) for k, w in want.items()})
        state['waiting_krw'] = max(0.0, state['waiting_krw'] - spend)
    if spend >= 1_000 or shares_buy:
        join_carry()
        state['last_buy'] = day
        state.pop('since', None)
    if state['waiting_krw'] < 1_000 and not by_master['when'] and not quantity:
        state.pop('since', None)
    orders = {k: v for k, v in orders.items() if v >= 1}
    if prices is not None:
        unpriced = {k: v for k, v in orders.items() if not prices.get(k)}
        small = {k: v for k, v in orders.items() if prices.get(k) and v < prices[k] * (1 + FEE)}
        orders = {k: v for k, v in orders.items() if k not in unpriced and k not in small}
        if unpriced:
            notes.append(f"시세가 없어 {', '.join(map(name, unpriced))} {sum(unpriced.values()):,.0f}원은 사지 않음(현금으로 남음)")
        # the part over whole shares is carried too (D5, 2026-11-20: 58,800원 at 23,315 bought 2 shares and 12,170원 left the 적립식)
        rest = {k: v - share_cost(prices[k], int(v // (prices[k] * (1 + FEE)))) for k, v in orders.items()}
        rest = {k: r for k, r in rest.items() if r >= 1}
        orders = {k: v - rest.get(k, 0) for k, v in orders.items()}
        small |= {k: small.get(k, 0) + r for k, r in rest.items()}
        if small:
            state['carry'] = {**state['carry'], **{k: state['carry'].get(k, 0) + v for k, v in small.items()}}
            notes.append('1주 미만이라 다음 적립 때 합쳐 사도록 이월: ' + ', '.join(f'{name(k)} {v:,.0f}원' for k, v in small.items()))
    orders, left = cap_shares(cfg, orders, shares, prices)
    if left >= 1:
        notes.append(f'주식 수 제한으로 {left:,.0f}원은 사지 않음(현금으로 남음)')
    if cfg['size'] == 'shares' and by_master['size'] and spend >= 1_000 and prices:  # the shares the master's amount buys, after the limits
        counts = {k: int(v // (prices[k] * (1 + FEE))) for k, v in orders.items() if prices.get(k)}
        notes.append('이번 수량(거장): ' + (', '.join(f'{name(k)} {n}주' for k, n in counts.items() if n) or '없음(1주 미만은 다음 매수로 넘김)'))
    if p['tuned'] and orders:
        notes.append('내 조정: ' + tune_text(cfg['tune']))
    return orders, state, notes


def size_text(cfg, p, regime, ind=None, value=None, yeo=False):
    """'다음 회차 약 23만 원(기본 16만 × 1.4배)' or my amount / shares, for the views and the chart."""
    if cfg['frequency'] == 'deposit':
        if cfg['size_by'] == 'me':
            return '입금 몫 전부를 한 번에'
        return f"입금 몫의 {cfg['deposit_pct']}%를 {cfg['spread_weeks']}주에 나눠 · 국면 배수 {multiplier(p, regime, ind)[0]:g}배"
    if cfg['size_by'] == 'me':
        return f"종목별 {cfg['shares_n']:,}주" if cfg['size'] == 'shares' else f"{cfg['amount_krw']:,}원"
    every = cfg['buy_every'] if cfg['when_by'] == 'master' else PER_PERIOD[cfg['frequency']]
    base = base_amount(cfg, p, every, value, yeo)
    mult, crash = multiplier(p, regime, ind)
    return (f"약 {min(base * mult, CAP * base):,.0f}원(기본 {base:,.0f} × {mult:g}배{' 급락일' if crash else ''})"
            + (' · 종목별 정수 주로' if cfg['size'] == 'shares' else ''))


def when_text(cfg, p):
    if cfg['when_by'] == 'master':
        how = '하락일 우선(최대 ' + f"{p['wait']}일)" if p['when'] == 'dip' else '주기마다'
        return f"거장이 매 거래일 확인 · {how} · {BUY_EVERY[cfg['buy_every']]} 한 번까지" + (' · 입금 몫이 있을 때' if cfg['frequency'] == 'deposit' else '')
    if cfg['frequency'] == 'deposit':
        return f"입금 몫이 있을 때 · {ME_WHENS[cfg['me_when']]}"
    cycle = {'monthly': f"매월 {cfg['day']}일", 'weekly': f"매주 {WEEKDAYS[cfg['weekday']]}요일", 'daily': '매일'}[cfg['frequency']]
    return f"{cycle} · {ME_WHENS[cfg['me_when']]}"


def effective(cfg, regime, ind, want, prices, cash=None, state=None, values=None, shares=None, now=None, yeo=False):
    """What a 적립식 setting does now, for the views: each part with who decides it and the value, and an example buy made by
    plan() itself as if today were a buy day (rows per ETF: weight, price, KRW, whole shares, carried part, left out by a limit)."""
    from vibe_longterm import FEE, KST, REGIMES as LR
    cfg = clean(cfg)
    p = rule(cfg, regime)
    label = LR[regime][0] if regime in LR else (regime or '국면 모름')
    value = (cash or 0) + sum((values or {}).values())
    picked = universe(cfg, want)
    from vibe_longterm import asset_name as name  # engine ETFs, 여운봉 ETFs (0261H0 …) and 관심목록 codes
    top = ', '.join(f'{name(k)} {v:.0%}' for k, v in sorted(picked.items(), key=lambda kv: -kv[1])[:3])
    fields = [
        {'key': 'source', 'label': '거장', 'value': f"{STYLES[cfg['style']]} · {p['label']}({p['title']})", 'default': '', 'tuned': False},
        {'key': 'when', 'label': f"언제 · {BY[cfg['when_by']]}", 'value': when_text(cfg, p), 'default': '', 'tuned': 'wait' in p['tuned']},
        {'key': 'size', 'label': f"얼마나 · {BY[cfg['size_by']]}", 'value': size_text(cfg, p, regime, ind, value, yeo), 'default': '',
         'tuned': bool({'rate', 'pace', 'mult'} & set(p['tuned']))},
        {'key': 'mult', 'label': f'오늘 국면({label}) 배수', 'value': f"{p['mult'].get(regime, 1.0):g}배" if cfg['size_by'] == 'master' else '쓰지 않음(내 금액)',
         'default': f"{PROFILES[p['key']]['mult'].get(regime, 1.0):g}배", 'tuned': 'mult' in p['tuned']},
        {'key': 'basis', 'label': f"무엇을 · {'수동 · 내가' if cfg['pick_on'] else '자동 · 계좌 전략'}", 'default': '', 'tuned': False,
         'value': (f"고른 종목 {len(picked)}개" if cfg['pick_on'] else f"계좌 전략이 사는 종목 — 지금 {top}") if picked else '살 종목 없음: 적립하지 않음'},
        {'key': 'time', 'label': f"몇 시 · {'자동 · 거장' if cfg['time_mode'] == 'auto' else '수동 · 내가'}", 'default': '', 'tuned': False,
         'value': f'장중 전일 종가 이하일 때 · 늦어도 {TIMES[1]}' if cfg['time_mode'] == 'auto' else cfg['time']},
        {'key': 'floor', 'label': '현금 하한(여운봉 계좌)', 'value': (f"{p['floor']:.0%}" if cfg['size_by'] == 'master' else '쓰지 않음(내 금액)') if yeo
         else '쓰지 않음(현금 비중은 엔진 저수지가 지킴)',
         'default': f"{PROFILES[p['key']]['floor']:.0%}", 'tuned': 'floor' in p['tuned']},
        {'key': 'modules', 'label': '파는 쪽', 'value': '계좌 전략(엔진 거장 모듈 또는 여운봉 저수지 매매법) · 적립식은 사기만 함', 'default': '', 'tuned': False},
        {'key': 'limits', 'label': '종목·수량 제한', 'default': '', 'tuned': False, 'value': ' · '.join(
            ([f"적립 종목 {len(cfg['picks'])}개만({', '.join(name(k) for k in cfg['picks'])})"] if cfg['pick_on'] else [])
            + ([f"최대 {cfg['max_symbols']}종목"] if cfg['max_symbols_on'] else [])
            + ([f"종목당 {cfg['max_per_symbol']:,}주"] if cfg['max_per_symbol_on'] else [])
            + ([f"전체 {cfg['max_total']:,}주"] if cfg['max_total_on'] else [])) or '없음'}]
    amount = contribution(cfg, want, prices)
    out = {'rule': p, 'fields': fields, 'amount': amount, 'orders': {}, 'rows': [], 'waiting': 0.0, 'notes': []}
    if not amount:
        missing = [name(k) for k in picked if not (prices or {}).get(k)]
        out['notes'] = [f"시세 기록이 없는 종목({', '.join(missing)})이 있어 예시를 계산하지 못함" if picked and missing else
                        '살 종목이 없어 적립하지 않음(계좌 전략·적립 종목 선택·종목 수 제한 확인)' if not picked else '예시를 계산하지 못함']
        return out
    at = (now or datetime.now(KST)).replace(hour=int(cfg['time'][:2]), minute=int(cfg['time'][3:]), second=0, microsecond=0)
    sim = {**(state or {}), 'last_check': None, 'last_buy': None}
    if cfg['frequency'] == 'deposit':  # the deposit money waiting now, else a sample deposit of 1,000,000원
        amount = (state or {}).get('waiting_krw', 0) if (state or {}).get('waiting_krw', 0) >= 1_000 else 1_000_000 * cfg['deposit_pct'] / 100
        sim = {**sim, 'waiting_krw': amount, 'deposit_base': amount / deposit_buys(cfg)}  # already in, nothing arrives
    room = amount + reserved(sim) if cash is None else cash
    arrived = 0 if cfg['frequency'] == 'deposit' else amount
    orders, after, notes = plan({**cfg, 'on': True}, sim, at, ind, regime, want, values or {}, room, arrived=arrived, shares=shares, prices=prices,
                                yeo=yeo, force=True)
    out.update(orders=orders, waiting=after['waiting_krw'], notes=['예시: 오늘이 사는 날이라고 가정한 계산입니다. 금액은 그날 계좌 가치·국면·적립식 몫으로 달라집니다.'
                                                                   + (f' 입금할 때: 입금 몫 {amount:,.0f}원 기준.' if cfg['frequency'] == 'deposit' else ''), *notes],
               after=after)
    before_carry = (sim.get('carry') or {})
    for k in dict.fromkeys([*want, *picked, *orders]):
        price, krw = (prices or {}).get(k), orders.get(k, 0)
        out['rows'].append({'key': k, 'name': name(k), 'weight': picked.get(k, 0), 'target': want.get(k, 0), 'price': price, 'krw': krw,
                            'qty': int(krw // (price * (1 + FEE))) if price and krw else 0,
                            'carry': (after.get('carry') or {}).get(k, 0) - before_carry.get(k, 0),
                            'excluded': k in want and want.get(k, 0) > 0 and k not in picked})
    return out


def buy_levels(cfg, ind, regime, state, price, close=None, value=None, yeo=False, today=None):
    """Where and how this 적립식 buys next, as price lines on an ETF's chart (user request 2026-11-18): a down-day way at
    yesterday's close, a cycle way at today's price with its next day, 윌리엄스's crash day −3%; each label carries the
    master, the next buy day and this round's size. [] without a price."""
    cfg = clean(cfg)
    if not price:
        return []
    p = rule(cfg, regime)
    size = size_text(cfg, p, regime, ind, value, yeo)
    who = p['label'] if cfg['style'] == 'master' else '수동'
    dip = (cfg['when_by'] == 'master' and p['when'] == 'dip') or (cfg['when_by'] == 'me' and cfg['me_when'] == 'dip')
    nxt = ('입금 몫이 있을 때' if cfg['frequency'] == 'deposit' else '거장이 매 거래일 확인' if cfg['when_by'] == 'master'
           else None) or f"다음 적립일 {next_day(cfg, today or date.today()).isoformat()}"
    out = []
    if dip and close:
        out.append({'price': close, 'status': 'waiting', 'kind': 'dip', 'label': f"{who} 적립 · 하락일(전일 종가 이하) · {size} · {nxt}"})
    elif not dip:
        out.append({'price': price, 'status': 'waiting', 'kind': 'cycle', 'label': f"{who} 적립 · 정한 주기(가격 무관) · {size} · {nxt}"})
    if p['crash']:
        out.append({'price': price * 0.97, 'status': 'waiting', 'kind': 'crash', 'label': f"{who} 급락일 1.5배 · 나스닥100 −3%·VIX≥25"})
    return out


def start_time(cfg):
    """From when a day's 적립식 may act: the set time, or (매수 시각 자동) the window's first minute."""
    return TIMES[0] if cfg.get('time_mode') == 'auto' else cfg['time']


def ready(cfg, ind, want, prices, now):
    """(매수 시각 자동) Buy now: a chosen equity ETF trades at or under yesterday's KRX close (ind krx_close), or it is the
    window's last check; a set time is ready once it has come (the caller gates it), and an unknown close is no reason to wait."""
    if cfg.get('time_mode') != 'auto' or now.strftime('%H:%M') >= TIMES[1]:
        return True
    close = (ind or {}).get('krx_close') or {}
    keys = [k for k in universe(cfg, want) if k != RESERVOIR and close.get(k)]
    if not keys:
        return True
    return any((prices or {}).get(k) and prices[k] <= close[k] for k in keys)


def auto_look(cfg, now):
    """(매수 시각 자동) A minute when the engine reads quotes to judge the time: every AUTO_CHECK_MIN minutes, and the last check."""
    return cfg.get('time_mode') != 'auto' or now.minute % AUTO_CHECK_MIN == 0 or now.strftime('%H:%M') >= TIMES[1]


def share_cost(price, n):
    """KRW that buys exactly n shares at price with the fee: rounded up to the won plus one, so a whole-share order sized by
    KRW // (price × (1 + fee)) never comes out one share short from float rounding."""
    from vibe_longterm import FEE
    return math.ceil(price * n * (1 + FEE)) + 1


def contribution(cfg, want, prices, stories=None):
    """KRW one contribution brings in: amount_krw (also for 종목별 수량 기준 with 얼마나 · 거장), or (수량 기준, 얼마나 · 내가) N shares of each ETF plan() buys at the day's buy price
    with the fee; None when such an ETF has no price today (the contribution then waits for the next trading day)."""
    cfg = clean(cfg)
    keys = list(buy_weights(cfg, want, stories=stories))
    if not keys:  # nothing the 적립식 may buy: no contribution (and no virtual deposit) today
        return None
    if cfg['size'] != 'shares' or cfg['size_by'] == 'master':  # the master sizes from 들어오는 적립금, what plan() counts
        return cfg['amount_krw']
    if any(not (prices or {}).get(k) for k in keys):
        return None
    return sum(share_cost(prices[k], cfg['shares_n']) for k in keys)


def sleeve(cfg):
    """The 관심목록 codes the setting buys (picks outside the engine's ETFs and the 여운봉 ETFs; picks count only with pick_on)."""
    from vibe_longterm import ASSETS, key_of
    return [k for k in cfg['picks'] if k not in ASSETS and key_of(k) == k] if cfg['pick_on'] else []


def reserved(state):
    """KRW of a book's cash that is the 적립식's, not the engine's: the waiting money, the carried parts under one share and
    the money an order did not buy (retry)."""
    state = state or {}
    return state.get('waiting_krw', 0) + sum((state.get('carry') or {}).values()) + sum((state.get('retry') or {}).values())


def alert(cfg, state, now, ind, regime, want, prices):
    """적립 알림 (user 2026-11-20): (rows [{key, KRW}], new alert state, notes) of what this 적립식 would buy today while it is
    off, for the user to buy in another account. The same plan() on its own imaginary book (state 'dca_alert'): its
    calendar or its master's day, its master's multiplier, the account strategy's ETFs, whole shares at today's prices,
    the 1,000,000원-a-month way sized by amount_krw (입금할 때 alerts as 매일 of that amount). Never an order."""
    cfg = clean(cfg)
    if not cfg['alert'] or cfg['on']:
        return {}, state or {}, []
    as_on = {**cfg, 'on': True, 'frequency': 'daily' if cfg['frequency'] == 'deposit' else cfg['frequency']}
    return plan(as_on, state or {}, now, ind, regime, want, {}, float('inf'), prices=prices)


def deposit_buys(cfg):
    """How many buys a deposit is spread over: spread_weeks of the master's buy_every (my day: every trading day)."""
    every = cfg['buy_every'] if cfg['when_by'] == 'master' else 'day'
    return max(1.0, cfg['spread_weeks'] * BUYS_PER_WEEK[every])


def take_deposit(cfg, state, capital, day, logged=True):
    """(new state, Korean note or '') for 입금할 때: the 운용금 a live account last saw (capital_seen) against its 운용금 now.
    The first look only sets it (the money already there is the strategy's reservoir); a raise adds deposit_pct of it to
    the 적립식's money and sets the per-buy base (the waiting money over deposit_buys); a cut takes it out of what the
    적립식 has not spent (never below zero, nothing is sold). logged False (no settings-history row for this 운용금): not
    taken, the note says so and the next look tries again."""
    cfg = clean(cfg)
    state = {'waiting_krw': 0.0, 'contributed_krw': 0.0, 'carry': {}, 'retry': {}, 'catch_krw': 0.0, **(state or {})}
    if cfg['frequency'] != 'deposit' or not cfg['on'] or type(capital) not in (int, float):
        return state, ''
    seen = state.get('capital_seen')
    if type(seen) not in (int, float):
        state['capital_seen'] = capital
        return state, f'입금 기준 운용금 {capital:,.0f}원 · 지금 있는 돈은 계좌 전략(저수지) 몫, 운용금을 올린 만큼부터 적립식 몫'
    delta = capital - seen
    if not delta:
        return state, ''
    if not logged:
        return state, f'운용금이 {seen:,.0f}원 → {capital:,.0f}원으로 바뀌었지만 설정 변경 이력이 없어 입금으로 반영하지 않음'
    state['capital_seen'] = capital
    if delta < 0:
        cut = min(state['waiting_krw'], -delta)
        state['waiting_krw'] -= cut
        state['deposit_base'] = state['waiting_krw'] / deposit_buys(cfg) if state['waiting_krw'] >= 1 else 0.0
        return state, f'운용금 {seen:,.0f}원 → {capital:,.0f}원: 남은 적립식 몫에서 {cut:,.0f}원 뺌(보유는 팔지 않음)'
    add = delta * cfg['deposit_pct'] / 100
    state['waiting_krw'] += add
    state['contributed_krw'] += add
    state['last_contribution'] = day
    state.setdefault('since', day)
    state['deposit_base'] = state['waiting_krw'] / deposit_buys(cfg)
    return state, (f"입금 반영: 운용금 {seen:,.0f}원 → {capital:,.0f}원 · 적립식 몫 +{add:,.0f}원(남은 몫 {state['waiting_krw']:,.0f}원, "
                   f"{cfg['spread_weeks']}주에 나눠 회당 약 {state['deposit_base']:,.0f}원)")


def add_unfilled(state, sent):
    """(new state, Korean note): the 적립식 money of sent buys that did not fill (cancelled or refused) kept as retry {key: KRW,
    fee included} for the next check, so it never mixes into the engine's cash (user approval 2026-11-18). Shadows fill at
    their own prices and never need it; a live account's order that was never sent leaves its contribution due instead."""
    from vibe_longterm import FEE, asset_name
    missed = {}
    for o in sent or []:
        if o.get('number') and 'filled' not in o:  # sent but never settled: it may have filled, so it is not bought again
            continue
        left = o.get('qty', 0) - (o.get('filled') or 0)
        if o.get('side') == 'BUY' and left > 0:
            missed[o['key']] = missed.get(o['key'], 0) + left * o['price'] * (1 + FEE)
    if not missed:
        return state, ''
    retry = dict((state or {}).get('retry') or {})
    for k, v in missed.items():
        retry[k] = retry.get(k, 0) + v
    names = ', '.join(f"{asset_name(k)} {v:,.0f}원" for k, v in missed.items())
    return {**(state or {}), 'retry': retry}, f'체결되지 않은 적립 {sum(missed.values()):,.0f}원({names})은 다음 확인 때 다시 삼'


def universe(cfg, want):
    """The target weights the 적립식 may buy: the chosen ETFs (pick_on) or today's targets above zero, then only the largest
    max_symbols. A chosen engine ETF stays even when the book's strategy targets 0% for it (적립식 and the 운용금 strategy are
    set apart, 2026-11-18: 실계좌 2 '여운봉 저수지만' holds only the reservoir) and then weighs its base weight. A chosen 관심목록
    code weighs the average of the chosen equity ETFs (else of today's equity targets, else of the base weights), so codes
    chosen alone split the money equally."""
    from vibe_longterm import ASSETS, key_of
    base = {k: a[3] for k, a in ASSETS.items()}
    picks = [key_of(k) for k in cfg['picks']]  # a picked 여운봉 ETF code (0261H0) is its order key (NDXMIX)
    from yeo_method import EXTRA
    yeo_picks = [k for k in picks if k in EXTRA] if cfg['pick_on'] else []
    if cfg['pick_on']:
        keys = [k for k in want if k in picks and (k in ASSETS or want[k] > 0)] + [k for k in picks if k in ASSETS and k not in want]
        out = {k: want.get(k, 0) if want.get(k, 0) > 0 else base[k] for k in keys}
    else:
        out = {k: v for k, v in want.items() if v > 0}
    equity = ([v for k, v in out.items() if k != RESERVOIR] or [v for k, v in want.items() if k != RESERVOIR and v > 0]
              or [w for k, w in base.items() if k != RESERVOIR])
    out |= {c: sum(equity) / len(equity) for c in [*sleeve(cfg), *(k for k in yeo_picks if k not in out)]}
    if cfg['max_symbols_on']:
        out = dict(sorted(out.items(), key=lambda item: -item[1])[:cfg['max_symbols']])
    return out


def cap_shares(cfg, orders, shares, prices):
    """(orders, KRW left unbought): each order cut to whole shares that keep a held ETF at or under max_per_symbol and
    all held shares at or under max_total. An ETF without a price is not bought while a share limit is on."""
    from vibe_longterm import FEE
    if not (cfg['max_per_symbol_on'] or cfg['max_total_on']):
        return orders, 0.0
    shares, prices = shares or {}, prices or {}
    total = cfg['max_total'] - sum(shares.values()) if cfg['max_total_on'] else float('inf')
    out, left = {}, 0.0
    for k, krw in sorted(orders.items(), key=lambda item: -item[1]):
        price = prices.get(k)
        room = min(total, cfg['max_per_symbol'] - shares.get(k, 0) if cfg['max_per_symbol_on'] else float('inf'))
        whole = int(krw // (price * (1 + FEE))) if price else 0  # as the order is sized
        qty = min(whole, int(max(room, 0)))
        if qty < whole or not price:  # a limit (or a missing price) cut it; a fraction of a share is not a limit
            left += krw - (share_cost(price, qty) if qty else 0)
        if qty:
            out[k] = krw if qty == whole else share_cost(price, qty)
            total -= qty
    return out, left


def period(cfg, when):
    """The contribution period a date falls in: '2026-11' (monthly), '2026-W47' (weekly, ISO week) or '2026-11-18' (daily)."""
    if cfg['frequency'] == 'daily':
        return when.strftime('%Y-%m-%d')
    if cfg['frequency'] == 'weekly':
        year, week, _ = when.isocalendar()
        return f'{year}-W{week:02d}'
    return when.strftime('%Y-%m')


def due(cfg, state, now):
    """The contribution of this period has not been made and its day and time have come: the set day, or the period's
    last trading day when no trading day is left on or after the set day (February 27~28, a holiday Friday), so no
    period passes without its contribution."""
    if not cfg['on'] or now.strftime('%H:%M') < start_time(cfg) or cfg['frequency'] == 'deposit':  # 입금할 때: take_deposit
        return False
    last = state.get('last_contribution')
    if last and period(cfg, date.fromisoformat(last)) == period(cfg, now):
        return False
    if cfg['frequency'] == 'daily':
        return True
    if now.weekday() >= cfg['weekday'] if cfg['frequency'] == 'weekly' else now.day >= cfg['day']:
        return True
    from vibe_longterm import trading_day  # here: vibe_longterm imports this module
    later = (now.date() + timedelta(days=i) for i in range(1, 32))
    return not any(trading_day(d) for d in takewhile(lambda d: period(cfg, d) == period(cfg, now), later))


def down_day(ind, want, prices):
    """A down day for the 적립식's waiting money: the Nasdaq-100 fell in its last session, or a chosen equity ETF trades at
    or under yesterday's KRX close. Unknown data is not a down day."""
    ret = (ind or {}).get('day_ret')
    if type(ret) in (int, float) and ret < 0:
        return True
    close = (ind or {}).get('krx_close') or {}
    return any((prices or {}).get(k) and close.get(k) and prices[k] <= close[k] for k in want if k != RESERVOIR)


def next_day(cfg, today):
    """The next set contribution day on or after `today` (calendar only; a holiday moves it to the period's next trading day)."""
    cfg = clean(cfg)
    if cfg['frequency'] in ('daily', 'deposit'):  # 입금할 때: whenever deposit money waits (the next trading day)
        return today + timedelta(days=max(0, 7 - today.weekday()) if today.weekday() >= 5 else 0)
    if cfg['frequency'] == 'weekly':
        return today + timedelta(days=(cfg['weekday'] - today.weekday()) % 7)
    if today.day <= cfg['day']:
        return today.replace(day=cfg['day'])
    return (today.replace(day=1) + timedelta(days=32)).replace(day=cfg['day'])
