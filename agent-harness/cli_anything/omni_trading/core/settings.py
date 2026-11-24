"""Every setting under one address, read and written through the functions the dashboard uses.

A change is planned first: the real save function runs on a temporary copy of the files, and what differs afterwards is the
plan (so a range, a cross rule, a value the function would move, and a linked change all show up before anything is written).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .output import Refused


@dataclass
class Spec:
    label: str
    default: object = None
    kind: str = 'number'   # number, int, bool, text, json
    low: object = None
    high: object = None
    choices: tuple = ()
    effect: str = ''


# The names a person uses (the dashboard's inputs): what `settings list --find` searches besides the address
ACCOUNT_LABELS = {
    'first_buy_krw': '분할 1회 매수 금액(원)', 'per_symbol_krw': '종목당 최대 매수 금액(원)', 'capital_krw': '운용금(원)', 'order_price_max_krw': '1주 가격 상한(원)',
    'daily_loss_krw': '하루 손실 한도(원)', 'order_max_krw': '회당 매수 한도(원)', 'daily_buy_krw': '하루 매수 한도(원)', 'held_max_krw': '보유 금액 한도(원)',
    'daily_new_symbols': '하루 새 종목 수', 'take_profit_pct': '익절 %(모의를 따르는 미러 계좌에서는 쓰지 않음)', 'stop_loss_pct': '손절 %', 'sell_confidence': '매도 신뢰도 기준(모의를 따르는 미러 계좌에서는 쓰지 않음)',
    'flatten': '장 마감 정리(모의를 따르는 미러 계좌에서는 쓰지 않음)', 'regime_exits': '국면별 청산(모의를 따르는 미러 계좌에서는 쓰지 않음)', 'overnight': '이월(모의를 따르는 미러 계좌에서는 쓰지 않음)',
    'follow_paper': '모의 체결 따라가기', 'sizing': '매수 크기 기준(1주씩·금액 비율)', 'tranche_krw': '한 번에 따라 살 최대 금액(원)', 'max_order_qty': '1회 매수 최대 수량(주)',
    'chase_pct': '가격 추격 한도 %', 'evidence_chase': '추격 한도를 넘어도 근거가 있으면 매수', 'max_adds': '추가 매수 최대 횟수', 'follow_pct': '모의 대비 금액 비율 %(비우면 자동)'}
PREF_LABELS = {'status_blink': '상태 점 깜빡임', 'start_tab': '시작 탭', 'refresh_s': '자동 갱신 간격(초)', 'quotes_auto': '시세 자동 갱신', 'money_unit': '금액 단위',
               'font_scale': '글자 크기 %', 'theme': '화면 색(밝게·어둡게)', 'ask_selection': '고른 글로 AI에게 묻기'}
YEO_LABELS = {'market': '여운봉: 시장', 'us_index': '여운봉: 미국 지수', 'mix_us': '여운봉: 미국·한국을 섞을 때 미국 몫', 'mix_ndx': '여운봉: 나스닥100·S&P500을 섞을 때 나스닥100 몫',
              'risk': '여운봉: 위험자산 비중', 'band': '여운봉: 리밸런싱 이탈 폭', 'steps': '여운봉: 미국 하락 단계와 투입 비율', 'kospi_steps': '여운봉: 코스피 하락 단계와 투입 비율',
              'park': '여운봉: 대기 현금을 저수지 ETF로'}


def parse(spec, raw, address):
    """`raw` text as the setting's type within its range; Refused otherwise (the save functions may only clamp)."""
    word = str(raw).strip()
    if word.lower() in ('none', 'null', '자동') and spec.kind.endswith('?'):
        return None
    kind = spec.kind.rstrip('?')
    try:
        if kind == 'bool':
            value = {'on': True, 'true': True, '1': True, '켬': True, 'off': False, 'false': False, '0': False, '끔': False}[word.lower()]
        elif kind == 'int':
            value = float(word.replace(',', ''))
            if value != int(value):
                raise ValueError
            value = int(value)
        elif kind == 'number' and set(spec.choices) == {0.0, 1.0} and word.lower() in ('on', 'off', 'true', 'false', '켬', '끔'):
            value = float(word.lower() in ('on', 'true', '켬'))
        elif kind == 'number':
            value = float(word.replace(',', ''))
            if not math.isfinite(value):
                raise ValueError
        elif kind == 'json':
            value = json.loads(word)
        else:
            value = word
    except (KeyError, ValueError, OverflowError):
        raise Refused(f'{address}: 값을 읽을 수 없습니다({raw}).') from None
    if spec.choices and value not in spec.choices:
        raise Refused(f'{address}: {" · ".join(map(str, spec.choices))} 중 하나여야 합니다.')
    if kind in ('int', 'number') and spec.low is not None and not spec.low <= value <= spec.high:
        raise Refused(f'{address}: {spec.low:,}~{spec.high:,} 사이여야 합니다.')
    if kind == 'text' and spec.low is not None and not str(spec.low) <= value <= str(spec.high):
        raise Refused(f'{address}: {spec.low}~{spec.high} 사이여야 합니다.')
    return value


def strict_json(path):
    """The file as JSON, {} when there is none; a file that cannot be read is refused: the loaders would fall back to
    their defaults and the next save would write those over what the user chose."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise Refused(f'{path.name}을 읽을 수 없습니다. 값을 확인하기 전에는 저장하지 않습니다.') from None
    if not isinstance(data, dict):
        raise Refused(f'{path.name} 형식이 올바르지 않습니다.')
    return data


@contextmanager
def _env(**values):
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def flat(value, prefix=''):
    if isinstance(value, dict) and value:
        return {k2: v2 for k, v in value.items() for k2, v2 in flat(v, f'{prefix}.{k}' if prefix else str(k)).items()}
    return {prefix: value}


class Group:
    name = kind = ''
    path = ''       # relative to nhplug

    def files(self, nh):
        return [nh / self.path]

    def specs(self, nh):
        return {}

    def read(self, nh):
        return {}

    def write(self, nh, changes, who):
        raise NotImplementedError


class Strategy(Group):
    name = kind = 'strategy'
    path = 'shared-etf-paper/strategy-settings.json'

    def specs(self, nh):
        import strategy_settings as S
        return {k: Spec(S.LABELS[k], v, 'number', *S.LIMITS[k], (0.0, 1.0) if k in S.SWITCH_KEYS else (), '다음 회차') for k, v in S.DEFAULTS.items()}

    def read(self, nh):
        import strategy_settings as S
        strict_json(nh / self.path)
        values, state = S.read_settings((nh / self.path).parent)
        if state == 'invalid':
            raise Refused('매매 기준 파일의 값이 올바르지 않아 실행기는 기본값을 쓰고 있습니다. 저장하기 전에 파일을 확인하세요.')
        return values

    def write(self, nh, changes, who):
        import strategy_settings as S
        S.write_settings((nh / self.path).parent, {**self.read(nh), **changes}, who=who)


class Tunables(Group):
    name = kind = 'tunables'
    path = 'tunables.json'

    def specs(self, nh):
        import tunables as T
        return {k: Spec(v[4], v[0], 'text' if isinstance(v[0], str) else 'int' if isinstance(v[0], int) else 'number', v[1], v[2], (), T.RESTART[v[3]])
                for k, v in T.TUNABLES.items()}

    def read(self, nh):
        import tunables as T
        strict_json(nh / self.path)
        with _env(VIBE_TRADING_TUNABLES=str(nh / self.path)):
            return dict(T.values())

    def write(self, nh, changes, who):
        import tunables as T
        if any(T.clean(k, v) != v for k, v in changes.items()):
            raise Refused('운영 조정값의 범위나 형식이 맞지 않습니다.')
        with _env(VIBE_TRADING_TUNABLES=str(nh / self.path)):
            T.save(changes, who=who)


class Prefs(Group):
    name = kind = 'prefs'
    path = 'dashboard-prefs.json'

    def specs(self, nh):
        import prefs as P
        return {k: Spec(f'화면: {PREF_LABELS.get(k, k)}', v, 'bool' if isinstance(v, bool) else 'int' if isinstance(v, int) else 'text', choices=tuple(P.CHOICES[k]), effect='바로')
                for k, v in P.DEFAULTS.items()}

    def read(self, nh):
        import prefs as P
        strict_json(nh / self.path)
        return P.load(nh / self.path)

    def write(self, nh, changes, who):
        import prefs as P
        P.save(nh / self.path, {**P.load(nh / self.path), **changes}, who=who)


class Ai(Group):
    name, kind = 'ai', 'ai_modes'
    path = 'shared-etf-paper/ai-modes.json'

    def files(self, nh):
        return [nh / self.path, nh / 'shared-etf-paper/ai-modes.pending.json']

    def specs(self, nh):
        return {'jev': Spec('Jev 판단 사용', True, 'bool', effect='주문 없는 회차'), 'decision': Spec('OpenAI Decisions 사용', True, 'bool', effect='주문 없는 회차')}

    def read(self, nh):
        import paper_ai_modes as M
        root = (nh / self.path).parent
        try:
            now = M.read_pending_modes(root) or M.read_modes(root)
        except ValueError:
            raise Refused('AI 사용 설정을 읽을 수 없습니다.') from None
        return {k: now[k] for k in ('jev', 'decision')}

    def write(self, nh, changes, who):
        import paper_ai_modes as M
        M.write_modes((nh / self.path).parent, **{**self.read(nh), **changes}, who=who)


class Accounts(Group):
    name = kind = 'accounts'
    path = 'accounts.json'

    def records(self, nh):
        import accounts as A
        strict_json(nh / self.path)
        return A.load(nh / self.path)

    def specs(self, nh):
        import accounts as A
        out = {}
        for key, record in self.records(nh).items():
            live = record['kind'] == 'live'
            out[f'{key}.auto'] = Spec('자동매매', False, 'bool', effect='다음 회차')
            out[f'{key}.principal_krw'] = Spec('원금(화면 손익 기준)', None, 'int', 1, 10 ** 12, effect='바로')
            if live:
                out[f'{key}.engine'] = Spec('운용 방식', 'jev', 'text', choices=A.ENGINES, effect='다음 회차')
            for part, limits, base in (('budget', A.BUDGET_LIMITS[record['kind']], A.DEFAULT_BUDGET[record['kind']]),
                                       ('limits', A.LIVE_LIMITS if live else A.PAPER_LIMITS, A.DEFAULT_LIVE_LIMITS if live else A.DEFAULT_PAPER_LIMITS)):
                for k, (low, high) in limits.items():
                    out[f'{key}.{part}.{k}'] = Spec(ACCOUNT_LABELS.get(k, k), base[k], 'int', low, high, effect='다음 회차')
            for k, v in (A.DEFAULT_LIVE_RULES if live else {}).items():
                low, high = A.LIVE_RULE_LIMITS.get(k, A.FOLLOW_PCT if k == 'follow_pct' else (None, None))
                kind = 'bool' if isinstance(v, bool) else 'text' if isinstance(v, str) else 'int' if isinstance(v, int) else 'number?' if v is None else 'number'
                out[f'{key}.rules.{k}'] = Spec(ACCOUNT_LABELS.get(k, k), v, kind, low, high, A.LIVE_SIZING if k == 'sizing' else (), '새 모의 체결부터' if k in ('sizing', 'tranche_krw', 'follow_pct', 'max_adds') else '다음 회차')
        return out

    def read(self, nh):
        import accounts as A
        out = {}
        for key, r in self.records(nh).items():
            if r.get('invalid'):
                raise Refused(f'{key} 계좌 설정이 올바르지 않습니다. 저장하기 전에 화면에서 확인하세요.')
            live = r['kind'] == 'live'
            out |= {f'{key}.auto': r['auto'], f'{key}.principal_krw': r.get('principal_krw'), f'{key}.engine': r['engine']}
            out |= {f'{key}.budget.{k}': v for k, v in r['budget'].items()}
            out |= {f'{key}.limits.{k}': v for k, v in (A.live_limits(record=r) if live else A.paper_limits(r)).items()}
            if live:
                out |= {f'{key}.rules.{k}': v for k, v in A.live_rules(r).items()} | {f'{key}.live_orders': r.get('live_orders') is True}
            else:
                out |= {f'{key}.match_live.{k}': v for k, v in flat(r.get('match_live') or {'account': None}).items()}
        return out

    def write(self, nh, changes, who):
        import accounts as A
        keys = {c.split('.', 1)[0] for c in changes}
        if len(keys) != 1:
            raise Refused('계좌 하나씩 계획하세요(두 계좌를 함께 저장하다가 중간에 멈출 수 있습니다).')
        key, record = keys.pop(), None
        record = self.records(nh)[key]
        live, out = record['kind'] == 'live', {}
        for address, value in changes.items():
            part, _, field = address.split('.', 1)[1].partition('.')
            if not field:
                out[part] = value
                continue
            base = {'budget': record['budget'], 'limits': A.live_limits(record=record) if live else A.paper_limits(record),
                    'rules': A.live_rules(record)}[part]
            out.setdefault(part, dict(base))[field] = value
        if live and out.get('budget', {}).get('capital_krw', 0) > record['budget']['capital_krw']:
            raise Refused('실계좌 운용금은 계좌 총자산을 확인할 수 있는 대시보드(계좌·자동매매)에서 올리세요.')
        A.save_one(key, out, nh / self.path, who=who)


class Match(Accounts):
    """account match: the paper account's money follows a live account."""
    name = 'match'

    def specs(self, nh):
        return {}

    def write(self, nh, changes, who):
        import accounts as A
        A.set_match(changes['account'], changes.get('positions', 2), changes['on'], nh / self.path, who=who)


class LongTerm(Group):
    kind = 'vibe-longterm'
    path = 'vibe-longterm/settings.json'

    def load(self, nh):
        import vibe_longterm as L
        strict_json(nh / self.path)
        return L.load_settings((nh / self.path).parent)

    def save(self, nh, part, who):
        import vibe_longterm as L
        L.save_settings((nh / self.path).parent, part, who=who)

    def books(self, nh):
        import accounts as A
        saved = self.load(nh)
        return sorted({'actual', *saved['books'], *saved['dca'], *A.longterm_accounts(Accounts().records(nh))})


class Engine(LongTerm):
    name = 'lt'

    def specs(self, nh):
        import vibe_longterm as L
        out = {'enabled': Spec('장기 엔진 운영', True, 'bool', effect='다음 확인'), 'mode': Spec('운용 모드', 'manual', 'text', choices=tuple(L.MODES), effect='다음 08:45'),
               'auto_live': Spec('자동 조합을 실제 적용', False, 'bool', effect='다음 08:45'), 'execute': Spec('가상 체결', False, 'bool', effect='다음 09:40')}
        out |= {f'weights.{k}': Spec(a[1], a[3], 'number', 0.0, 0.6, effect='다음 08:45') for k, a in L.ASSETS.items()}
        out |= {f'tuning.{k}': Spec(v[3], v[0], 'int' if isinstance(v[0], int) else 'number', v[1], v[2], effect='다음 08:45') for k, v in L.TUNING.items()}
        out |= {f'modules.{k}': Spec(f'{m[0]} · {m[1]}', m[4], 'bool', effect='다음 08:45') for k, m in L.MODULES.items() if not m[5]}
        out |= {f'params.{m}.{k}': Spec(v[3], v[0], 'int' if isinstance(v[0], int) else 'number', v[1], v[2], effect='다음 08:45')
                for m, ps in L.MODULE_PARAMS.items() for k, v in ps.items()}
        return out

    def read(self, nh):
        s = self.load(nh)
        return flat({k: s[k] for k in ('enabled', 'mode', 'auto_live', 'execute', 'weights', 'tuning', 'modules', 'params')})

    def write(self, nh, changes, who):
        import vibe_longterm as L
        part = {}
        for k, v in changes.items():
            node, *rest = k.split('.')
            if not rest:
                part[node] = v
                continue
            target = part.setdefault(node, dict(L.load_settings((nh / self.path).parent)['weights']) if node == 'weights' else {})
            for step in rest[:-1]:
                target = target.setdefault(step, {})
            target[rest[-1]] = v
        self.save(nh, part, who)


class Book(LongTerm):
    name = 'book'

    def specs(self, nh):
        import vibe_longterm as L
        import yeo_method as Y
        out = {}
        for b in self.books(nh):
            out |= {f'{b}.strategy': Spec('전략', 'engine', 'text', choices=tuple(L.BOOK_STRATEGIES), effect='다음 계획'),
                    f'{b}.reservoir': Spec('저수지 ETF', 'auto', 'text', choices=tuple(L.RESERVOIR_CHOICES), effect='다음 계획')}
            out |= {f'{b}.modules.{m}': Spec(L.MODULES[m][0], None, 'bool', effect='다음 계획') for m in L.MODULES if not L.MODULES[m][5]}
            for k, v in Y.DEFAULT.items():
                low, high = Y.RANGES.get(k, (None, None))
                out[f'{b}.yeo.{k}'] = Spec(YEO_LABELS.get(k, k), v, 'bool' if isinstance(v, bool) else 'json' if isinstance(v, list) else 'text' if isinstance(v, str) else 'number',
                                          low, high, tuple(Y.MARKETS) if k == 'market' else tuple(Y.US_INDEX) if k == 'us_index' else (), '다음 계획')
        return out

    def read(self, nh):
        import vibe_longterm as L
        saved, out = self.load(nh)['books'], {}
        for b in self.books(nh):
            book = L.clean_book(saved.get(b))
            out |= {f'{b}.strategy': book['strategy'], f'{b}.reservoir': book['reservoir'], **{f'{b}.yeo.{k}': v for k, v in book['yeo'].items()},
                    **{f'{b}.modules.{m}': (book['modules'] or {}).get(m) for m in L.MODULES}}
        return out

    def write(self, nh, changes, who):
        part, saved = {}, self.load(nh)
        for k, v in changes.items():
            b, node, *rest = k.split('.')
            if rest:
                # a book that has no set of its own yet starts from the engine's, as on the screen
                base = dict(saved['modules']) if node == 'modules' and not (saved['books'].get(b) or {}).get('modules') else {}
                part.setdefault(b, {}).setdefault(node, base)[rest[0]] = v
            else:
                part.setdefault(b, {})[node] = v
        self.save(nh, {'books': part}, who)


class Dca(LongTerm):
    name = 'dca'

    def specs(self, nh):
        import longterm_dca as D
        import longterm_dca_guide as G
        labels = {row[0]: row[1] for row in G.SETTINGS}
        choices = {'profile': D.PROFILES, 'when_by': D.BY, 'size_by': D.BY, 'me_when': D.ME_WHENS, 'buy_every': D.BUY_EVERY,
                   'frequency': D.FREQUENCIES, 'size': D.SIZES, 'style': D.STYLES, 'time_mode': D.TIME_MODES}
        ranges = {'amount_krw': D.AMOUNT, 'shares_n': D.SHARES_N, 'deposit_pct': D.DEPOSIT_PCT, 'spread_weeks': D.SPREAD_WEEKS,
                  'day': (1, 28), 'weekday': (0, 4), 'time': D.TIMES, **D.LIMITS}
        out = {}
        for b in self.books(nh):
            for k, v in {**D.DEFAULT, 'style': 'master'}.items():
                if k in ('tune', 'pick_from'):
                    continue
                low, high = ranges.get(k, (None, None))
                out[f'{b}.{k}'] = Spec(f"적립식: {labels.get(k) or labels.get(f'{k}_on', k)}", v, 'bool' if isinstance(v, bool) else 'int' if isinstance(v, int) else 'json' if isinstance(v, list) else 'text',
                                       low, high, tuple(choices.get(k, ())), '다음 확인')
            for k, (low, high) in D.TUNE.items():
                for name in ([f'mult.{r}' for r in D.REGIMES] if k == 'mult' else [k]):
                    out[f'{b}.tune.{name}'] = Spec(f'거장 값 조정 {name}', None, 'int?' if k == 'wait' else 'number?', low, high, effect='다음 확인')
        return out

    def read(self, nh):
        import longterm_dca as D
        saved, out = self.load(nh)['dca'], {}
        for b in self.books(nh):
            cfg = D.clean(saved.get(b))
            tune = flat(cfg['tune']) if cfg['tune'] else {}
            out |= {f'{b}.{k}': v for k, v in cfg.items() if k not in ('tune', 'pick_from')}
            out |= {f'{b}.tune.{n}': tune.get(n) for k in D.TUNE for n in ([f'mult.{r}' for r in D.REGIMES] if k == 'mult' else [k])}
        return out

    def write(self, nh, changes, who):
        import longterm_dca as D
        saved, part = self.load(nh)['dca'], {}
        for k, v in changes.items():
            b, node, *rest = k.split('.')
            if node != 'tune':
                part.setdefault(b, {})[node] = v
                continue
            tune = part.setdefault(b, {}).setdefault('tune', json.loads(json.dumps(D.clean(saved.get(b))['tune'])))
            target = tune.setdefault('mult', {}) if rest[0] == 'mult' else tune
            target.pop(rest[-1], None) if v is None else target.__setitem__(rest[-1], v)
        self.save(nh, {'dca': part}, who)


class Watch(Group):
    """Hand edits of a trading watchlist: watch.<account>.add.<code> = name, watch.<account>.remove.<code>."""
    name = 'watch'

    def files(self, nh):
        import watchlist as W
        return [W.manual_path(k, nh / 'shared-etf-paper') for k in Accounts().records(nh)]

    def read(self, nh):
        import watchlist as W
        out = {}
        for key, path in zip(Accounts().records(nh), self.files(nh)):
            strict_json(path)
            state = W.manual(path)
            out |= {f'{key}.add.{c}': n for c, n in state['add'].items()} | {f'{key}.remove.{c}': True for c in state['remove']}
        return out

    def write(self, nh, changes, who):
        import watchlist as W
        for address, value in changes.items():
            account, op, code = address.split('.')
            W.edit_manual(**({'add': code, 'name': value} if op == 'add' else {'remove': code}), path=W.manual_path(account, nh / 'shared-etf-paper'), who=who)


class Universe(Group):
    """낙폭과대 감시 종목: universe.<code> = name, or None to drop it."""
    name = 'universe'
    path = 'shared-etf-paper/oversold-universe.json'

    def read(self, nh):
        import oversold
        strict_json(nh / self.path)
        return dict(oversold.universe((nh / self.path).parent))

    def write(self, nh, changes, who):
        import oversold
        merged = {**self.read(nh), **changes}
        oversold.save_universe({c: n for c, n in merged.items() if n is not None}, (nh / self.path).parent, who=who)


class DcaList(Group):
    """장기투자 적립식 관심목록: dcalist.<KRX code> = name, or None to drop it."""
    name = 'dcalist'
    path = 'vibe-longterm/longterm-watchlist.json'

    def read(self, nh):
        import longterm_watchlist as W
        strict_json(nh / self.path)
        return dict(W.load((nh / self.path).parent))

    def write(self, nh, changes, who):
        import longterm_watchlist as W
        listed = self.read(nh)
        for code, name in changes.items():
            if name is not None and (problem := W.problem(code, listed)):
                raise Refused(problem)
            listed = {k: v for k, v in listed.items() if k != code} | ({code: name} if name is not None else {})
        W.save((nh / self.path).parent, listed, who=who)


GROUPS = {g.name: g for g in (Strategy(), Accounts(), Engine(), Book(), Dca(), Tunables(), Ai(), Prefs())}
LISTS = {g.name: g for g in (Match(), Watch(), Universe(), DcaList())}
ALL = {**GROUPS, **LISTS}
SAME_FILE = {'lt': ('lt', 'book', 'dca'), 'book': ('lt', 'book', 'dca'), 'dca': ('lt', 'book', 'dca'), 'match': ('accounts',)}
BLOCKED = {'live_orders': '실주문 스위치는 설정 변경으로 바꿀 수 없습니다. `omni live off <계좌>`·`omni live on <계좌>`를 쓰세요.',
           'hash': '계좌 식별값은 바꿀 수 없습니다.', 'kind': '계좌 종류는 바꿀 수 없습니다.', 'masked': '계좌 표시값은 바꿀 수 없습니다.',
           'match_live': '모의 금액 맞춤은 `omni account match`를 쓰세요.', 'label': '계좌 이름은 바꿀 수 없습니다.'}


def split(address):
    name, _, key = address.partition('.')
    if name not in GROUPS or not key:
        raise Refused(f'알 수 없는 설정입니다: {address}. 묶음: {", ".join(GROUPS)}')
    if name == 'accounts':
        for word in key.split('.')[1:2]:
            if word in BLOCKED:
                raise Refused(BLOCKED[word])
    return GROUPS[name], key


def spec_of(group, nh, key):
    specs = group.specs(nh)
    if key not in specs:
        import difflib
        near = difflib.get_close_matches(key, specs, 3)
        raise Refused(f'알 수 없는 설정입니다: {group.name}.{key}', next=[f'{group.name}.{n}' for n in near])
    return specs[key]


def snapshot(group, nh):
    """{full address: value} of every group that shares this group's file (linked changes show up here)."""
    out = {}
    for name in SAME_FILE.get(group.name, (group.name,)):
        out |= {f'{name}.{k}': v for k, v in ALL[name].read(nh).items()}
    return out


def stamp(group, nh):
    """What the files hold now: a plan made on other contents must not be applied."""
    found = hashlib.sha256()
    for path in group.files(nh):
        found.update(str(path.name).encode())
        found.update(path.read_bytes() if path.exists() else b'-')
    return found.hexdigest()


def diff(before, after):
    return [[k, before.get(k), after.get(k)] for k in sorted(set(before) | set(after)) if before.get(k) != after.get(k)]


def rehearse(group, nh, changes):
    """The change made by the real save function on a copy: [[address, before, after]]; Refused when it would not do
    exactly what was asked."""
    before = snapshot(group, nh)
    with tempfile.TemporaryDirectory(prefix='omni-') as tmp, _env(VIBE_TRADING_SETTINGS_HISTORY=''):
        copy = Path(tmp) / 'nhplug'
        for path in {*group.files(nh), nh / 'accounts.json', nh / 'vibe-longterm/settings.json'}:
            target = copy / path.relative_to(nh)
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                shutil.copy2(path, target)
        try:
            group.write(copy, changes, 'plan')
        except ValueError as exc:
            raise Refused(str(exc)) from None
        after = snapshot(group, copy)
    if group.name in GROUPS:
        moved = [f'{group.name}.{k}: {v} → {after.get(f"{group.name}.{k}")}' for k, v in changes.items() if after.get(f'{group.name}.{k}') != v]
        if moved:
            raise Refused('그대로 저장되지 않는 값입니다(' + ', '.join(moved) + '). 범위나 다른 설정과의 조건을 확인하세요.')
    return diff(before, after)


def live_accounts(nh):
    """{engine: [live account keys whose real-order switch is on]}; None when the accounts cannot be read."""
    try:
        records = Accounts().records(nh)
    except Refused:
        return None
    out = {'jev': [], 'longterm': []}
    for key, r in records.items():
        if r.get('kind') == 'live' and (r.get('live_orders') is True or r.get('invalid')):
            out.setdefault(r.get('engine'), []).append(key)
    return out


def level(nh, changed):
    """('W2' or 'H', affected live accounts) of these [[address, before, after]]. Unreadable accounts count as live."""
    live = live_accounts(nh)
    if live is None:
        return 'H', ['확인 불가']
    hit = set()
    tunable_groups = None
    for address, before, after in changed:
        group, _, key = address.partition('.')
        account = key.split('.')[0]
        if group == 'prefs' or (group == 'accounts' and key.endswith('.principal_krw')):
            continue
        if group in ('accounts', 'book', 'dca', 'watch') and account not in ('paper-shared', 'actual'):
            hit.add(account)
        elif group in ('lt', 'book', 'dca', 'dcalist'):
            hit.update(live['longterm'])
            if group == 'lt' and key in ('execute', 'auto_live') and after is True:
                hit.add('확인 문구 대상')
        elif group == 'tunables':
            if tunable_groups is None:
                import tunables as T
                tunable_groups = {k: v[3] for k, v in T.TUNABLES.items()}
            if tunable_groups.get(key) not in ('대시보드', 'vibe-trading'):
                hit.update(live['jev'])
        else:
            hit.update(live['jev'])
    return ('H' if hit else 'W2'), sorted(hit)


def reach(nh, affects):
    """How each affected live account would feel the change, in words."""
    live = live_accounts(nh) or {}
    words = {'jev': '공유 모의계좌의 체결을 따라 주문하므로, 모의가 사고파는 시점이나 크기가 바뀌면 이 실계좌 주문도 바뀝니다.',
             'longterm': '장기 엔진 판단과 이 계좌 설정으로 주문을 계획하므로, 다음 계획부터 실계좌 주문이 바뀔 수 있습니다.'}
    return [f'{key}: {text}' for engine, text in words.items() for key in live.get(engine, ()) if key in affects]


def find(address_of_history, kind):
    """The CLI address of a settings-history row, or None when it is not one setting."""
    if kind in ('strategy', 'tunables', 'prefs', 'accounts'):
        return f'{kind}.{address_of_history}'
    if kind == 'ai_modes':
        return f'ai.{address_of_history}'
    if kind == 'vibe-longterm' and not address_of_history.startswith('watchlist'):
        head, _, rest = address_of_history.partition('.')
        return f'book.{rest}' if head == 'books' else f'dca.{rest}' if head == 'dca' else f'lt.{address_of_history}'
    return None
