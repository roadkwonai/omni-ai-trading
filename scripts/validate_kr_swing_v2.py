"""Run the frozen KR trend experiment on a validated local daily snapshot."""
from __future__ import annotations
import json
import hashlib
import sys
from pathlib import Path
import pandas as pd
from backtest.engines.korea_equity import KoreaEquityEngine
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategies.kr_swing_v2.signal_engine import SignalEngine

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / '.venv/kr-swing-data'
OUT = ROOT / 'results/kr-swing-v2'
PROTOCOL = json.loads((ROOT / 'strategies/kr_swing_v2/protocol.json').read_text())


class CappedEngine(KoreaEquityEngine):
    def __init__(self, config, quota=None):
        super().__init__(config)
        self.quota, self.buy_total, self.buy_by_day = quota, 0.0, {}

    def _plan_open_order(self, symbol, target_weight, df, ts, equity, **kwargs):
        return super()._plan_open_order(symbol, min(target_weight, 3_000_000 / equity), df, ts, equity, **kwargs)

    def _execute_open_order(self, order, ts):
        notional = order.price * order.size
        day = str(ts.date())
        if notional + self.buy_by_day.get(day, 0) > 6_000_000 + 1e-6:
            return
        if self.quota is not None and self.buy_total + notional > self.quota + 1e-6:
            return
        super()._execute_open_order(order, ts)
        self.buy_total += notional
        self.buy_by_day[day] = self.buy_by_day.get(day, 0) + notional


class Loader:
    name = 'local_validated_yahoo_snapshot'
    def __init__(self, data): self.data = data
    def fetch(self, codes, start_date, end_date, **kwargs):
        return {c: self.data[c].loc[start_date:end_date].copy() for c in codes}


class StaticSignal:
    def __init__(self, evaluation_start): self.start = pd.Timestamp(evaluation_start)
    def generate(self, data):
        return {c: pd.Series((f.index >= self.start).astype(float) * .1, index=f.index) for c, f in data.items()}


def validate_snapshot(directory, codes):
    metadata = json.loads((directory / 'metadata.json').read_text())
    frames = {}
    for code in codes:
        path = directory / f'{code}.csv'
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata['symbols'][code]['clean_sha256']:
            raise ValueError(f'snapshot hash mismatch: {code}')
        frame = pd.read_csv(path, parse_dates=['date']).set_index('date')
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing or len(frame) != metadata['common_rows']:
            raise ValueError(f'invalid snapshot calendar: {code}')
        if str(frame.index[0].date()) != metadata['common_first'] or str(frame.index[-1].date()) != metadata['common_last']:
            raise ValueError(f'invalid snapshot bounds: {code}')
        if set(frame.index.strftime('%Y-%m-%d')) & set(metadata['excluded_dates']):
            raise ValueError(f'quarantined date present: {code}')
        if frames and not frame.index.equals(next(iter(frames.values())).index):
            raise ValueError(f'snapshot calendars differ: {code}')
        frames[code] = frame
    return frames


def run():
    codes = PROTOCOL['universe']
    snapshot = validate_snapshot(DATA, [*codes, '069500.KS'])
    data = {c: snapshot[c] for c in codes}
    common = data[codes[0]].index
    if any(not f.index.equals(common) for f in data.values()): raise ValueError('daily calendars differ')
    heldout = common[-60]
    before = common[common < heldout][-1]
    periods = {'design': ('2021-01-01', '2023-12-31'), 'validation': ('2024-01-01', '2024-12-31'),
               'out_of_sample': ('2025-01-01', str(before.date())), 'historical_replay': (str(heldout.date()), '2026-11-05')}
    OUT.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for label, (start, end) in periods.items():
        warm = common[common < pd.Timestamp(start)][-120]
        for mode in ('strategy', 'quota', 'static', 'stress'):
            costs = dict(PROTOCOL['costs']['base'])
            if mode == 'stress': costs = {k: v * 2 for k, v in costs.items()}
            cfg = {'codes': codes, 'start_date': str(warm.date()), 'end_date': end,
                   'evaluation_start_date': start, 'source': 'local', 'initial_cash': 30_000_000, **costs}
            engine = CappedEngine(cfg, quota=21_000_000 if mode == 'quota' else None)
            signal = StaticSignal(start) if mode == 'static' else SignalEngine(start)
            metrics = engine.run_backtest(cfg, Loader(data), signal, OUT / label / mode)
            outputs[f'{label}_{mode}'] = {'metrics': metrics, 'buy_notional': engine.buy_total,
                                          'daily_max': max(engine.buy_by_day.values(), default=0)}
    benchmark_code = '069500.KS'
    benchmark = snapshot[benchmark_code]
    if not benchmark.index.equals(common):
        raise ValueError('benchmark calendar differs')
    class ETFSignal:
        def __init__(self, start): self.start = pd.Timestamp(start)
        def generate(self, frames):
            return {c: pd.Series((f.index >= self.start).astype(float) * .5, index=f.index) for c, f in frames.items()}
    for label, (start, end) in periods.items():
        warm = common[common < pd.Timestamp(start)][-120]
        cfg = {'codes': [benchmark_code], 'start_date': str(warm.date()), 'end_date': end,
               'evaluation_start_date': start, 'source': 'local', 'initial_cash': 30_000_000,
               'kr_brokerage': .002, 'kr_tax_sell': 0, 'slippage': .0005}
        engine = KoreaEquityEngine(cfg)
        metrics = engine.run_backtest(cfg, Loader({benchmark_code: benchmark}), ETFSignal(start), OUT / label / 'etf_reference')
        outputs[f'{label}_etf_reference'] = {'metrics': metrics,
            'note': 'Hypothetical initial 50% ETF allocation; no strategy order caps; split/dividend-adjusted price reference, not official total-return index; ETF sell tax set to zero.'}
    (OUT / 'summary.json').write_text(json.dumps(outputs, ensure_ascii=False, indent=2, default=str))
    oos = outputs['out_of_sample_strategy']['metrics']
    static = outputs['out_of_sample_static']['metrics']
    stress = outputs['out_of_sample_stress']['metrics']
    quota = outputs['out_of_sample_quota']['metrics']
    checks = {'oos_total_return_positive': oos['total_return'] > 0,
              'oos_max_drawdown_at_most_15pct': abs(oos['max_drawdown']) <= .15,
              'oos_completed_trades_at_least_20': oos['trade_count'] >= 20,
              'oos_sharpe_above_risk_matched_static_basket': False,
              'cost_stress_total_return_positive': stress['total_return'] > 0,
              'current_quota_oos_return_positive': quota['total_return'] > 0}
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    summary = {'promotion_passed': all(checks.values()), 'checks': checks, 'mode': 'research_only',
               'gate_notes': {'oos_sharpe_above_risk_matched_static_basket': 'not evaluated: static initial 50% portfolio drifts and is not risk-matched'},
               'reference_sharpe': {'strategy': oos['sharpe'], 'initial_50pct_static_drifting': static['sharpe']},
               'periods': periods, 'source': 'Yahoo adjusted OHLCV, common-date snapshot',
               'operating_limits': PROTOCOL['operating_limits'],
               'protocol_sha256': digest(ROOT / 'strategies/kr_swing_v2/protocol.json'),
               'data_sha256': {c: digest(DATA / f'{c}.csv') for c in [*codes, benchmark_code]},
               'metadata_sha256': digest(DATA / 'metadata.json'),
               'limitations': ['Each interval resets capital and quota; this is not a continuous six-year account.',
                   'Quota tracks filled buys, not cancelled or external submissions; no NH paper loss/session guards modeled.',
                   'Static basket starts at 50% target, enters up to 6000000 KRW per day, and then its weight drifts; not constant-risk matched.',
                   'Historical replay is not forward paper performance.',
                   'Yahoo adjusted prices and quarantined sessions limit historical execution fidelity.']}
    (OUT / 'validation_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(outputs, ensure_ascii=False, default=str))

if __name__ == '__main__': run()
