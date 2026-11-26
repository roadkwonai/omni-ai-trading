"""User-editable buy/sell thresholds for the shared ETF paper runner (strategy-settings.json).

The dashboard writes the file; the runner re-reads it every cycle. A missing or invalid file means the
approved defaults below, so a bad edit can never loosen the rules silently.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

FILE = 'strategy-settings.json'
DEFAULTS = {
    'entry_confidence': 0.5,      # Jev candidate confidence for the first buy (with data confirmation, user 2026-11-11)
    'sell_confidence': 0.7,       # Jev holding SELL confidence that sells everything
    'take_profit_half_pct': 1.0,  # sell half of the bought shares at average +X%
    'take_profit_all_pct': 2.0,   # sell the rest at average +X%
    'stop_loss_pct': 2.0,         # sell everything at average -X%
    'add_step_pct': 0.7,          # price-step adds use this distance from the last confirmed fill
    # Trend profile (strong or rising market): first take-profit and trailing stop from the peak; 2x ETFs use x1.5.
    'trend_half_pct': 2.0,
    'trend_trail_pct': 1.5,
    'overnight_max_manwon': 300,  # cost of positions that may stay past the close, in 10,000 KRW; 0 = always sell
    'swing_max_days': 5,          # a closing trade may keep riding a strong tape for at most this many sessions (user 2026-11-12)
    # Watchlist strength entry (when Jev waits): Jev's top-N watchlist picks with at least this share, up this much today.
    'strength_top_n': 3,          # 0 turns the strength entry off
    'strength_min_prob': 0.15,
    'strength_change_min': 2.0,
    'strength_change_max': 25.0,
    'strength_review_trial_on': 0,  # strict review only in non-executing ETF7 rehearsal
    # New-listing entry (user 2026-11-12: IPO momentum can pay even in a falling market). Own exit: stop, half, trail.
    'ipo_days': 2,                # sessions since listing that count (1 = listing day only; 0 turns it off)
    'ipo_size_pct': 50,           # share of the first-buy amount
    'ipo_near_high_pct': 3.0,     # price within this % of today's high
    'ipo_stop_pct': 3.0,
    'ipo_half_pct': 3.0,
    'ipo_trail_pct': 2.0,
    # Weak market (하락장 and not a strong day; mirrored for inverse): take profit early (user 2026-11-12).
    'weak_half_pct': 1.0,         # sell half here at the latest
    'weak_all_pct': 2.0,          # sell the rest here at the latest
    'fade_exit_pct': 0.8,         # a strength pick this % up in a 하락장·횡보장 is sold whole when its tape turns (0 = off)
    'entry_volume_ratio': 1.2,    # every entry path: last 5 one-minute volumes vs the 5 before; 0 = no volume check
    'direction_switch': 1,        # 1: when Jev picks the other direction in a market that backs it, close a losing opposite holding
    # Restriction switches (1 on, 0 off; user 2026-11-13: every restriction visible and releasable; restrictions.py lists them).
    'reentry_guard': 1,           # strength route re-buys a stock that lost or hit its trailing stop today only above that exit
    'reentry_allow': 1,           # 0: a stock whose round trip closed today is not bought again today (any normal entry path)
    # Careful re-entry (user 2026-11-18): a stock traded today and bought again in a regime against the position (long in
    # 횡보장·하락장, inverse in 상승장·횡보장) must not be chasing or weakening, needs a Jev re-entry review and buys smaller.
    'careful_reentry_on': 1,
    'careful_reentry_vwap_pct': 2.0,   # not more than this % above today's VWAP (and not below it)
    'careful_reentry_range_pct': 85,   # not at or above this % of today's range (0 = low, 100 = high)
    'careful_reentry_conf': 0.6,       # Jev re-entry review: BUY at this confidence or more
    'careful_reentry_size_pct': 50,    # share of the usual buy
    'losing_streak_guard': 1,     # 3 losing round trips in a row today: no new entry for 30 minutes
    'averaging_down_guard': 1,    # no 2nd/3rd buy against a weak market
    'opposite_guard': 1,          # no long and inverse at the same time (direction_switch works through it)
    'direction_confirm': 1,       # a Jev pick also needs its index moving the same way
    'rising_check': 1,            # every entry path: price above 5 minutes ago (user 2026-11-13: may be switched off)
    'ipo_add_guard': 1,           # no 2nd/3rd buy into a new listing
    'ipo_max_positions': 1,       # new listings held at once
    'ipo_slot_on': 1,             # new listings are checked every cycle even when Jev picks an ETF, and an inverse held does not block them (user 2026-11-17)
    'ipo_daily_max': 2,           # new-listing buys per day
    'ipo_time_stop_min': 60,      # a new-listing position still open after this many minutes is sold (0 = off)
    'strength_slot_on': 0,        # strength slot: a watchlist strength stock checked every cycle beside Jev's pick (off: decide with the 11-30 strength_shadow review)
    'strength_slot_max': 2,       # strength-slot stocks held at once
    'strength_slot_daily': 3,     # strength-slot buys per day
    'scale_signal_add_on': 1,     # a new engine BUY decision may add to a held ordinary position
    'scale_step_add_on': 1,       # a confirmed price-step signal may add to a held ordinary position
    'scale_profit_rebuy_on': 1,   # allow adds after verified partial take-profit
    'scale_interval_min': 5,      # minimum time since the last fill for price-step adds
    'scale_max_adds': 2,          # adds after the first buy per position, every add path (user 2026-11-17; 2 = 3 tranches)
    'size_holding_on': 1,         # buys shrink past half of the per-symbol limit (position_sizing, user 2026-11-17)
    'size_market_on': 1,          # buys shrink against the index regime, to these shares (settings, user 2026-11-24):
    'size_market_flat_pct': 75,   # ... in a 횡보장
    'size_market_against_pct': 50,  # ... against the buy (long in a 하락장, inverse in a 상승장)
    'regime_trim_on': 1,          # a held position sells half once when its index regime turns against it
    'add_conf_sizing_on': 1,      # an engine-decision add must beat the last buy (add_reason)
    'add_size_pct': 50,           # an engine-decision add buys this % of a tranche (100 = a full tranche)
    'partial_sell_conf': 0.55,    # a Jev SELL from this confidence up to sell_confidence sells part of the position once
    'partial_sell_pct': 33,       # ... this % of it
    # 낙폭과대 반등 (user 2026-11-13; oversold.py). Defaults from scripts/oversold_research.py (2y daily + 60d 5-minute
    # Yahoo bars, 6 instruments): a weak, small-sample edge only for deep falls of stocks and KODEX 200; none for 2x ETFs.
    'oversold_on': 1,             # 1: the route runs (Jev and vibe-trading)
    'oversold_stock_drop_pct': 6.0,   # trigger for stocks: today's low this % under the previous close ...
    'oversold_etf_drop_pct': 6.0,     # ... for a 1x ETF
    'oversold_lev_on': 0,         # 2x ETFs: no edge found at any setting, off unless switched on
    'oversold_lev_drop_pct': 8.0,     # ... for a 2x ETF when switched on
    'oversold_atr_mult': 1.5,     # ... and at least this many times the 20-day average true range
    'oversold_calm_min': 20,      # the fall stopped: no new low in the last N completed minutes ...
    'oversold_rebound_pct': 0.3,  # ... and the price at least this % off the low
    'oversold_size_pct': 100,     # buy size: % of one tranche; research: worst adverse moves -4.5% (stocks), keep ~1x
    'oversold_tp_pct': 1.5,       # take profit at +this % ...
    'oversold_recover_pct': 100,  # ... or when this % of the gap (previous close - low) is recovered, whichever is lower
    'oversold_stop_pct': 0.5,     # stop: this % under the day's low
    'oversold_market_drop_pct': 3.0,  # buy only when the code's market low is this % under its close (0 = off; user 2026-11-17)
    'oversold_hold_days': 1,      # sessions the rebound may take (0: sell by the flatten like any trade)
    'oversold_jev_min': 0.5,      # Jev must choose BUY with at least this confidence
    'oversold_vibe_min': 0.2,     # vibe-trading's score for the code must be at least this
    # 선별 v2 낙주매매 (user 2026-11-13: enter in paper now and gather data; then "fast judgment, split entry, split
    # take-profit for big gains"). oversold.v2_gate decides the day; the numbers come from the replay of 28 gate days
    # (docs/oversold-case-study.md 2-2): buy late (the 14-15h bar beat every earlier hour and a volume-bounce signal),
    # half on the day and half the next session if lower, half sold at +8%, the rest at +15% or after 3 sessions.
    'oversold_v2_on': 1,
    'oversold_v2_from_hhmm': 1430,      # buy window opens (it may run past the entry cutoff) ...
    'oversold_v2_until_hhmm': 1455,     # ... and closes (the runner stops at its session close)
    'oversold_v2_index_drop_pct': 8.0,  # the market (KODEX 200 / 코스닥150 low) down this much ...
    'oversold_v2_capit_drop_pct': 5.0,  # ... or this much after the index fell ...
    'oversold_v2_prior5_pct': 5.0,      # ... this % over the five sessions before (capitulation)
    'oversold_v2_rel_max_pct': 4.0,     # skip a code that fell this many %p more than its market (its own bad news)
    'oversold_v2_add_on': 1,            # buy the second half the next session when it trades lower (above the stop)
    'oversold_v2_half_tp_pct': 8.0,     # sell half at +this % of the average ...
    'oversold_v2_tp_pct': 15.0,         # ... and the rest at +this %
    'oversold_v2_hold_days': 3,         # otherwise sold at the flatten of this session (weekends/holidays not counted)
    # user 2026-11-17: a fast stop this % under the average buy price, then buy again at a held low (v2_reentry.py)
    'oversold_v2_stop_pct': 2.0,
    # user 2026-11-24 (삼성전자, "시황 보고 판단해서 매도"): off by default, the 2026-11-13 rule (stop, target or sessions only)
    'oversold_v2_jev_sell_on': 0,       # in profit, a Jev holding SELL at sell_confidence sells
    'oversold_v2_trail_at_pct': 0.0,    # once the best bid is +this % over the average (0 = off) ...
    'oversold_v2_trail_pct': 2.0,       # ... sell at this % under that high
    'oversold_v2_reentry_max': 2,       # re-buys after a stop per position (0 = off)
    'oversold_v2_reentry_hold_min': 20, # the low since the stop must hold this many minutes ...
    'oversold_v2_reentry_bounce_pct': 1.0,  # ... and the price be this % off it
    'oversold_v2_size_pct': 100,        # both halves together, % of one tranche
    'oversold_v2_veto_conf': 0.7,       # research veto: Jev WAIT at this confidence (1.0 = never veto) ...
    'oversold_v2_vibe_veto': -0.3,      # ... or a vibe-trading score at or under this skips the code
    'oversold_v2_boost_pct': 100,       # size when the research strongly agrees (Jev BUY >= 0.8 / vibe >= 0.5); 100 = no boost
    'oversold_v2_max_new': 3,           # new v2 codes per day
    # Day clock (user 2026-11-13; session_times.py): no new buys from the cutoff, same-day flatten from the flatten time.
    'entry_cutoff_hhmm': 1430,
    'flatten_hhmm': 1440,
}
SWITCH_KEYS = ('direction_switch', 'reentry_guard', 'reentry_allow', 'careful_reentry_on', 'losing_streak_guard', 'averaging_down_guard', 'opposite_guard', 'direction_confirm',
               'rising_check', 'ipo_add_guard', 'ipo_slot_on', 'strength_slot_on', 'oversold_on', 'oversold_lev_on', 'oversold_v2_on', 'oversold_v2_jev_sell_on',
               'scale_signal_add_on', 'scale_step_add_on', 'scale_profit_rebuy_on', 'add_conf_sizing_on', 'size_holding_on',
               'size_market_on', 'regime_trim_on')
LIMITS = {
    'entry_confidence': (0.3, 0.95), 'sell_confidence': (0.3, 0.95),
    'take_profit_half_pct': (0.2, 10.0), 'take_profit_all_pct': (0.3, 20.0),
    'stop_loss_pct': (0.5, 10.0), 'add_step_pct': (0.2, 5.0),
    'trend_half_pct': (0.5, 15.0), 'trend_trail_pct': (0.5, 10.0),
    'overnight_max_manwon': (0, 500_000),  # up to 50억 원 like the paper held-amount limit (user 2026-11-13); the order guard still caps holdings
    'swing_max_days': (1, 10),
    'strength_top_n': (0, 10), 'strength_min_prob': (0.05, 0.6), 'strength_change_min': (0.0, 10.0), 'strength_change_max': (5.0, 29.0),
    'strength_review_trial_on': (0, 1),
    'ipo_days': (0, 5), 'ipo_size_pct': (10, 100), 'ipo_near_high_pct': (0.5, 15.0), 'ipo_stop_pct': (1.0, 10.0),
    'ipo_half_pct': (1.0, 30.0), 'ipo_trail_pct': (0.5, 15.0),
    'weak_half_pct': (0.3, 5.0), 'weak_all_pct': (0.5, 10.0), 'fade_exit_pct': (0.0, 5.0), 'direction_switch': (0, 1), 'entry_volume_ratio': (0.0, 3.0),
    'careful_reentry_on': (0, 1), 'careful_reentry_vwap_pct': (0.5, 10.0), 'careful_reentry_range_pct': (50, 100),
    'careful_reentry_conf': (0.3, 0.95), 'careful_reentry_size_pct': (10, 100),
    'reentry_guard': (0, 1), 'reentry_allow': (0, 1), 'losing_streak_guard': (0, 1), 'averaging_down_guard': (0, 1), 'opposite_guard': (0, 1), 'direction_confirm': (0, 1),
    'rising_check': (0, 1), 'ipo_add_guard': (0, 1), 'ipo_max_positions': (1, 3),
    'ipo_slot_on': (0, 1), 'ipo_daily_max': (1, 10), 'ipo_time_stop_min': (0, 390),
    'strength_slot_on': (0, 1), 'strength_slot_max': (1, 5), 'strength_slot_daily': (1, 10),
    'scale_signal_add_on': (0, 1), 'scale_step_add_on': (0, 1), 'scale_profit_rebuy_on': (0, 1),
    'scale_interval_min': (1, 60), 'scale_max_adds': (0, 100), 'size_holding_on': (0, 1), 'size_market_on': (0, 1), 'size_market_flat_pct': (25, 100), 'size_market_against_pct': (25, 100), 'regime_trim_on': (0, 1), 'add_conf_sizing_on': (0, 1), 'add_size_pct': (10, 100), 'partial_sell_conf': (0.5, 0.95), 'partial_sell_pct': (10, 90),
    'oversold_on': (0, 1), 'oversold_stock_drop_pct': (1.0, 30.0), 'oversold_etf_drop_pct': (0.5, 15.0), 'oversold_lev_on': (0, 1),
    'oversold_lev_drop_pct': (1.0, 30.0), 'oversold_atr_mult': (0.0, 5.0), 'oversold_calm_min': (1, 25), 'oversold_rebound_pct': (0.0, 5.0),  # calm: the runners read 30 one-minute bars
    'oversold_size_pct': (50, 1000), 'oversold_tp_pct': (0.3, 15.0), 'oversold_recover_pct': (20, 100), 'oversold_stop_pct': (0.1, 5.0), 'oversold_market_drop_pct': (0.0, 15.0),
    'oversold_hold_days': (0, 3), 'oversold_jev_min': (0.0, 0.95),
    'oversold_vibe_min': (-1.0, 1.0),
    'oversold_v2_on': (0, 1), 'oversold_v2_from_hhmm': (900, 1510), 'oversold_v2_until_hhmm': (930, 1515),
    'oversold_v2_index_drop_pct': (3.0, 20.0),
    'oversold_v2_capit_drop_pct': (2.0, 15.0), 'oversold_v2_prior5_pct': (0.0, 20.0), 'oversold_v2_rel_max_pct': (0.5, 15.0),
    'oversold_v2_add_on': (0, 1), 'oversold_v2_half_tp_pct': (2.0, 30.0),
    'oversold_v2_jev_sell_on': (0, 1), 'oversold_v2_trail_at_pct': (0.0, 30.0), 'oversold_v2_trail_pct': (0.5, 15.0),
    'oversold_v2_stop_pct': (1.0, 25.0), 'oversold_v2_tp_pct': (3.0, 45.0), 'oversold_v2_hold_days': (1, 10),
    'oversold_v2_size_pct': (10, 1000), 'oversold_v2_veto_conf': (0.5, 1.0), 'oversold_v2_vibe_veto': (-1.0, 0.0),
    'oversold_v2_boost_pct': (100, 200), 'oversold_v2_max_new': (1, 6),
    'oversold_v2_reentry_max': (0, 3), 'oversold_v2_reentry_hold_min': (5, 60), 'oversold_v2_reentry_bounce_pct': (0.2, 3.0),
    'entry_cutoff_hhmm': (1000, 1500), 'flatten_hhmm': (1400, 1505),  # flatten 15:05 at the latest leaves 13 minutes to sell
}
LABELS = {
    'entry_confidence': 'Jev 매수 신뢰도', 'sell_confidence': 'Jev 매도 신뢰도',
    'take_profit_half_pct': '방어형 1차 익절 % (데이터 목표 없을 때·절반)', 'take_profit_all_pct': '방어형 2차 익절 % (데이터 목표 없을 때·나머지)',
    'stop_loss_pct': '손절 %', 'add_step_pct': '추가 매수 간격 %',
    'trend_half_pct': '추세형 1차 익절 %', 'trend_trail_pct': '추세형 고점 대비 추적 손절 %',
    'overnight_max_manwon': '장 마감 보유 한도 (만원)', 'swing_max_days': '스윙 최대 보유일 (거래일)',
    'strength_top_n': '관심종목 강도 진입: Jev 순위 상위 N위 (0=끔)', 'strength_min_prob': '관심종목 강도 진입: Jev 확률 최소',
    'strength_change_min': '관심종목 강도 진입: 전일 대비 최소 상승 %', 'strength_change_max': '관심종목 강도 진입: 전일 대비 최대 상승 %',
    'strength_review_trial_on': '주문 없는 모의 검증에서만 관심종목 강도 진입 보호 적용 (1=켬)',
    'ipo_days': '신규주: 상장 후 거래일 수 (0=끔)', 'ipo_size_pct': '신규주: 분할 1회 매수 금액 대비 %',
    'ipo_near_high_pct': '신규주: 당일 고가 대비 허용 % 이내', 'ipo_stop_pct': '신규주: 손절 %',
    'ipo_half_pct': '신규주: 절반 익절 %', 'ipo_trail_pct': '신규주: 고점 대비 추적 손절 %',
    'weak_half_pct': '약세장: 절반 익절 % (늦어도)', 'weak_all_pct': '약세장: 전량 익절 % (늦어도)',
    'fade_exit_pct': '하락·횡보장 강도 종목: 매수세가 꺾이면 전량 매도하는 수익 % (0=끔)',
    'direction_switch': '방향 전환 (1=켬, 0=끔)', 'entry_volume_ratio': '진입 거래량 배수 (최근 5분 ÷ 그 전 5분, 0=확인 안 함)',
    'careful_reentry_on': '신중 재진입 (불리한 국면의 같은 날 재진입)', 'careful_reentry_vwap_pct': '신중 재진입: VWAP 대비 최대 %',
    'careful_reentry_range_pct': '신중 재진입: 오늘 범위 위치 상한 %', 'careful_reentry_conf': '신중 재진입: Jev 재검토 매수 신뢰도',
    'careful_reentry_size_pct': '신중 재진입: 매수 크기 (평소 대비 %)',
    'reentry_guard': '같은 날 재진입 제한', 'reentry_allow': '같은 날 재진입 허용', 'losing_streak_guard': '연속 손실 쉬기', 'averaging_down_guard': '약세장 물타기 금지',
    'opposite_guard': '반대 방향 동시 보유 차단', 'direction_confirm': '지수 방향 확인 (Jev 경로)',
    'rising_check': '최근 분봉 상승 확인', 'ipo_add_guard': '신규주 추가 매수 금지', 'ipo_max_positions': '신규주 동시 보유 종목 수',
    'ipo_slot_on': '신규주 독립 슬롯 (Jev 선택·인버스 보유와 별도)', 'ipo_daily_max': '신규주 하루 매수 횟수', 'ipo_time_stop_min': '신규주 시간 손절 분 (0=끔)',
    'strength_slot_on': '관심종목 강도 독립 슬롯 (Jev 선택과 별도)', 'strength_slot_max': '강도 슬롯 동시 보유 종목 수', 'strength_slot_daily': '강도 슬롯 하루 매수 횟수',
    'scale_signal_add_on': '새 매수 판단으로 보유 종목 추가 매수',
    'scale_step_add_on': '가격 단계 도달 시 보유 종목 추가 매수',
    'scale_profit_rebuy_on': '일부 익절 후 재매수',
    'scale_interval_min': '가격 단계 추가 매수 최소 간격(분)', 'scale_max_adds': '종목당 추가 매수 최대 횟수(첫 매수 제외)', 'add_conf_sizing_on': '추가 매수는 직전보다 나은 조건일 때만 (1=켬)', 'add_size_pct': '추가 매수 크기 (새 판단·가격 단계, 분할 1회 대비 %)', 'partial_sell_conf': '엔진 일부 매도 시작 신뢰도', 'partial_sell_pct': '엔진 일부 매도 비율 %', 'size_holding_on': '매수 비중: 보유가 종목당 한도 절반을 넘으면 줄이기 (1=켬)', 'size_market_on': '매수 비중: 시황 방향 배율 (같은 방향은 100%, 1=켬)',
    'size_market_flat_pct': '매수 비중: 횡보장 크기 %', 'size_market_against_pct': '매수 비중: 반대 방향 크기 % (하락장 일반 매수·상승장 인버스)', 'regime_trim_on': '시황 역전 시 보유 절반 매도 (포지션당 1번, 1=켬)',
    'entry_cutoff_hhmm': '신규 매수 마감 시각', 'flatten_hhmm': '장 마감 정리 시각',
    'oversold_on': '낙폭과대 반등 매매', 'oversold_stock_drop_pct': '낙폭과대: 개별주 낙폭 % (전일 대비 저가)',
    'oversold_etf_drop_pct': '낙폭과대: ETF 낙폭 %', 'oversold_lev_drop_pct': '낙폭과대: 2배 ETF 낙폭 %',
    'oversold_atr_mult': '낙폭과대: 평소 변동폭(ATR) 배수', 'oversold_calm_min': '낙폭과대: 저가 갱신 없는 분',
    'oversold_rebound_pct': '낙폭과대: 저가 대비 반등 %', 'oversold_size_pct': '낙폭과대: 매수 크기 (분할 1회 대비 %)',
    'oversold_tp_pct': '낙폭과대: 익절 %', 'oversold_recover_pct': '낙폭과대: 익절 (낙폭 회복 %, 둘 중 낮은 값)',
    'oversold_stop_pct': '낙폭과대: 손절 (당일 저가 아래 %)', 'oversold_market_drop_pct': '낙폭과대: 시장 저가 하락 % 이상일 때만 (0=조건 없음)', 'oversold_lev_on': '낙폭과대: 2배 ETF 포함',
    'oversold_hold_days': '낙폭과대: 최대 보유 거래일 (0=당일)', 'oversold_jev_min': '낙폭과대: Jev 매수 신뢰도',
    'oversold_vibe_min': '낙폭과대: vibe 점수',
    'oversold_v2_on': '낙폭과대 선별 v2 매매', 'oversold_v2_from_hhmm': '선별 v2: 매수 시작 시각',
    'oversold_v2_until_hhmm': '선별 v2: 매수 마감 시각', 'oversold_v2_add_on': '선별 v2: 다음 날 2차 매수 (1=켬)',
    'oversold_v2_half_tp_pct': '선별 v2: 절반 익절 %', 'oversold_v2_veto_conf': '선별 v2: 리서치 거부 (Jev WAIT 신뢰도)',
    'oversold_v2_vibe_veto': '선별 v2: 리서치 거부 (vibe 점수 이하)', 'oversold_v2_boost_pct': '선별 v2: 리서치 강한 동의 시 크기 %',
    'oversold_v2_index_drop_pct': '선별 v2: 지수 폭락 % (저가)', 'oversold_v2_capit_drop_pct': '선별 v2: 투매 지수 낙폭 %',
    'oversold_v2_prior5_pct': '선별 v2: 투매 직전 5일 지수 하락 %', 'oversold_v2_rel_max_pct': '선별 v2: 시장보다 더 빠진 한도 %p',
    'oversold_v2_jev_sell_on': '선별 v2: 수익 중 Jev 매도 판단으로 팔기 (1=켬)',
    'oversold_v2_trail_at_pct': '선별 v2: 고점 되밀림 매도 시작 % (평균가 대비, 0=끔)', 'oversold_v2_trail_pct': '선별 v2: 고점에서 되밀림 %',
    'oversold_v2_stop_pct': '선별 v2: 손절 (평균 매입가 대비 %)', 'oversold_v2_tp_pct': '선별 v2: 나머지 익절 %',
    'oversold_v2_hold_days': '선별 v2: 보유 거래일', 'oversold_v2_size_pct': '선별 v2: 매수 크기 (1·2차 합계, 분할 1회 대비 %)',
    'oversold_v2_max_new': '선별 v2: 하루 매수 종목 수',
    'oversold_v2_reentry_max': '선별 v2: 손절 뒤 저점 재매수 횟수 (0=끔)',
    'oversold_v2_reentry_hold_min': '선별 v2: 재매수 전 저점 유지 분',
    'oversold_v2_reentry_bounce_pct': '선별 v2: 재매수 저점 반등 %',
}


def problems(values):
    """Human-readable reasons the values are unusable; empty when valid."""
    issues = []
    for key, (low, high) in LIMITS.items():
        value = values.get(key) if isinstance(values, dict) else None
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            issues.append(f'{LABELS[key]}는 {low}~{high} 사이여야 합니다.')
    if issues:
        return issues
    if values['take_profit_half_pct'] >= values['take_profit_all_pct']:
        issues.append('1차 익절은 2차 익절보다 낮아야 합니다.')
    if values['trend_half_pct'] < values['take_profit_half_pct']:
        issues.append('추세형 1차 익절은 기본 1차 익절보다 낮을 수 없습니다.')
    if values['strength_change_min'] >= values['strength_change_max']:
        issues.append('관심종목 강도 진입의 최소 상승 %는 최대 상승 %보다 작아야 합니다.')
    if values['strength_top_n'] != int(values['strength_top_n']):
        issues.append('관심종목 강도 진입 순위는 정수여야 합니다.')
    if values['weak_half_pct'] >= values['weak_all_pct']:
        issues.append('약세장 절반 익절은 전량 익절보다 낮아야 합니다.')
    for key in SWITCH_KEYS:
        if values[key] not in (0, 1):
            issues.append(f'{LABELS[key]}은(는) 0 또는 1이어야 합니다.')
    if values['ipo_days'] != int(values['ipo_days']):
        issues.append('신규주 상장 후 거래일 수는 정수여야 합니다.')
    if values['ipo_max_positions'] != int(values['ipo_max_positions']):
        issues.append('신규주 동시 보유 종목 수는 정수여야 합니다.')
    for key in ('entry_cutoff_hhmm', 'flatten_hhmm', 'oversold_v2_from_hhmm', 'oversold_v2_until_hhmm'):
        if values[key] != int(values[key]) or int(values[key]) % 100 >= 60:
            issues.append(f'{LABELS[key]}이 올바른 시각(예: 1430)이 아닙니다.')
    for key in ('swing_max_days', 'scale_interval_min', 'scale_max_adds', 'ipo_daily_max', 'strength_slot_max', 'strength_slot_daily', 'ipo_time_stop_min',
                'add_size_pct', 'size_market_flat_pct', 'size_market_against_pct', 'oversold_calm_min', 'oversold_hold_days', 'oversold_size_pct', 'oversold_v2_hold_days', 'oversold_v2_size_pct',
                'oversold_v2_max_new', 'oversold_v2_reentry_max', 'oversold_v2_reentry_hold_min'):
        if values[key] != int(values[key]):
            issues.append(f'{LABELS[key]}는 정수여야 합니다.')
    if values['entry_cutoff_hhmm'] > values['flatten_hhmm']:
        issues.append('신규 매수 마감은 장 마감 정리 시각보다 늦을 수 없습니다.')
    if values['oversold_v2_from_hhmm'] >= values['oversold_v2_until_hhmm']:
        issues.append('선별 v2 매수 시작 시각은 마감 시각보다 빨라야 합니다.')
    if values['oversold_v2_half_tp_pct'] > values['oversold_v2_tp_pct']:
        issues.append('선별 v2 절반 익절 %는 나머지 익절 %보다 클 수 없습니다.')
    return issues


def read_settings(root):
    """(settings, status): status is 'default', 'custom' or 'invalid' (invalid falls back to defaults)."""
    try:
        stored = json.loads((Path(root) / FILE).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return dict(DEFAULTS), 'default'
    except (OSError, ValueError):
        return dict(DEFAULTS), 'invalid'
    # a file saved before a setting existed keeps its values; the new setting starts at its default
    values = {key: stored.get(key, DEFAULTS[key]) for key in DEFAULTS} if isinstance(stored, dict) else {}
    if problems(values):
        return dict(DEFAULTS), 'invalid'
    return {key: float(value) for key, value in values.items()}, 'custom'


def write_settings(root, values, who='dashboard'):
    issues = problems(values)
    if issues:
        raise ValueError(' '.join(issues))
    import config_store
    before = read_settings(root)[0]
    config_store.write_json(Path(root) / FILE, {**{k: float(values[k]) for k in DEFAULTS},
                                                 'updated': datetime.now(ZoneInfo('Asia/Seoul')).isoformat()}, indent=None)
    config_store.record('strategy', before, {k: float(values[k]) for k in DEFAULTS}, who, saved=Path(root) / FILE)


# Ready-made choices for the watchlist strength entry (dashboard presets; the user can still edit each value).
STRENGTH_PRESETS = {
    '보수적': {'strength_top_n': 1, 'strength_min_prob': 0.30, 'strength_change_min': 3.0, 'strength_change_max': 20.0},
    '기본': {'strength_top_n': 3, 'strength_min_prob': 0.15, 'strength_change_min': 2.0, 'strength_change_max': 25.0},
    '적극적': {'strength_top_n': 5, 'strength_min_prob': 0.10, 'strength_change_min': 1.0, 'strength_change_max': 29.0},
    '끄기': {'strength_top_n': 0, 'strength_min_prob': 0.15, 'strength_change_min': 2.0, 'strength_change_max': 25.0},
}

IPO_PRESETS = {
    '보수적': {'ipo_days': 1, 'ipo_size_pct': 30, 'ipo_near_high_pct': 2.0, 'ipo_stop_pct': 2.0, 'ipo_half_pct': 3.0, 'ipo_trail_pct': 1.5},
    '기본': {'ipo_days': 2, 'ipo_size_pct': 50, 'ipo_near_high_pct': 3.0, 'ipo_stop_pct': 3.0, 'ipo_half_pct': 3.0, 'ipo_trail_pct': 2.0},
    '적극적': {'ipo_days': 3, 'ipo_size_pct': 70, 'ipo_near_high_pct': 5.0, 'ipo_stop_pct': 4.0, 'ipo_half_pct': 5.0, 'ipo_trail_pct': 3.0},
    '끄기': {'ipo_days': 0, 'ipo_size_pct': 50, 'ipo_near_high_pct': 3.0, 'ipo_stop_pct': 3.0, 'ipo_half_pct': 3.0, 'ipo_trail_pct': 2.0},
}

VOLUME_PRESETS = {'엄격': 1.5, '기본': 1.2, '완화': 1.0, '끄기': 0.0}  # entry_volume_ratio choices shown on the dashboard

# 낙폭과대 반등 choices (dashboard cards; scripts/oversold_research.py found a weak edge only for deep falls).
_OVERSOLD_BASE = {k: DEFAULTS[k] for k in DEFAULTS if k.startswith('oversold_')}
OVERSOLD_PRESETS = {
    '보수적': {**_OVERSOLD_BASE, 'oversold_stock_drop_pct': 8.0, 'oversold_etf_drop_pct': 7.0, 'oversold_atr_mult': 2.0,
            'oversold_calm_min': 25, 'oversold_rebound_pct': 0.5, 'oversold_hold_days': 0, 'oversold_jev_min': 0.6, 'oversold_vibe_min': 0.3},
    '기본': dict(_OVERSOLD_BASE),
    '적극적': {**_OVERSOLD_BASE, 'oversold_stock_drop_pct': 4.0, 'oversold_etf_drop_pct': 3.0, 'oversold_atr_mult': 1.0,
            'oversold_calm_min': 10, 'oversold_size_pct': 200, 'oversold_tp_pct': 3.0, 'oversold_stop_pct': 1.0,
            'oversold_jev_min': 0.4, 'oversold_vibe_min': 0.1, 'oversold_lev_on': 1, 'oversold_lev_drop_pct': 8.0},
    '끄기': {**_OVERSOLD_BASE, 'oversold_on': 0, 'oversold_v2_on': 0},
}
