"""전략 구조 탭 하단 부품: 청산 방식 카드, 국면×오늘 흐름 대응 카드, 진입 경로 카드, 조건 체크리스트. HTML만 만들고
값은 strategy_map.regime_rows·설정에서 받는다(규칙 자체는 바꾸지 않음)."""
from __future__ import annotations

from html import escape

import settings_view as sv

# Korean market colours: rising red, falling blue, flat amber.
REGIMES = {'상승장': ('#ef4444', 'trending_up'), '횡보장': ('#f59e0b', 'trending_flat'), '하락장': ('#3b82f6', 'trending_down')}
EXITS = {'추세형': ('#f97316', 'moving'), '방어형': ('#38bdf8', 'shield'), '약세장': ('#a78bfa', 'south')}
ON, OFF, MAYBE = '#22c55e', '#ef4444', '#f59e0b'

CSS = """<style>
.sv-check{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:6px 10px;margin:4px 0 8px;padding:0;list-style:none;counter-reset:c}
.sv-check li{counter-increment:c;display:flex;gap:8px;align-items:flex-start;border:1px solid rgba(148,163,184,.2);border-radius:10px;
  padding:8px 10px;font-size:.86rem;line-height:1.4;word-break:keep-all}
.sv-check li::before{content:counter(c);flex:none;width:22px;height:22px;border-radius:50%;display:grid;place-items:center;
  font-size:.75rem;font-weight:700;color:#0b1020;background:var(--c,#22c55e)}
.sv-head{display:flex;align-items:center;gap:6px;font-weight:700;font-size:1.05rem;color:var(--c);margin:2px 0 6px}
.sv-head .ms{font-family:'Material Symbols Rounded';font-size:1.3rem}
.sv-arrow{display:grid;place-items:center;height:100%;min-height:120px;font-family:'Material Symbols Rounded';font-size:28px;color:#64748b}
</style>"""


def exit_kind(text):
    """'추세형: ...' -> ('추세형', '...')."""
    head, _, rest = str(text).partition(': ')
    return (head, rest) if head in EXITS else ('방어형', str(text))


def exit_cards(settings):
    """The three exit styles with the numbers in force (title, rows, colour, icon, note)."""
    s = settings
    return [
        ('추세형', [('절반 익절', f"+{s['trend_half_pct']:g}%"), ('나머지', f"고점 −{s['trend_trail_pct']:g}% 추적"),
                   ('손절', f"상승장 −{s['stop_loss_pct']:g}% · 횡보·하락장 −{min(s['stop_loss_pct'], 1.0):g}%")], '상승장, 또는 횡보·하락장의 강세일. 수익을 길게 끌고 갑니다(2배 ETF 추적 폭 ×1.5).'),
        ('방어형', [('익절', '데이터 목표에서 전량'), ('데이터 없으면', f"+{s['take_profit_half_pct']:g}% 절반 · +{s['take_profit_all_pct']:g}% 나머지"),
                   ('손절', f"상승장 −{s['stop_loss_pct']:g}% · 횡보·하락장 −{min(s['stop_loss_pct'], 1.0):g}%")], '횡보장 등 기본. 목표 = 최근 20일 장중 상승폭 중앙값 × 국면 계수(0.8~3%).'),
        ('약세장', [('절반 익절', f"늦어도 +{s.get('weak_half_pct', 1.0):g}%"), ('전량 익절', f"늦어도 +{s.get('weak_all_pct', 2.0):g}%"),
                   ('손절', f"−{min(s['stop_loss_pct'], 1.0):g}% · 본전")], '하락장의 혼조·약세일. 반등을 짧게 먹고 빠집니다(롱 추가 매수 중단).'),
    ]


def regime_card(row, current=False, last=False):
    """One 국면×오늘 흐름 cell as a card: exit style colour, add-buy chips, exit and close rules."""
    kind, rest = exit_kind(row['청산'])
    color, icon = EXITS[kind]
    tone = lambda value: ON if value == '허용' else MAYBE if value == '조건부' else OFF  # noqa: E731
    chips = [(f"{kind} 청산", color), (f"롱 추가 {row['추가 매수(롱)']}", tone(row['추가 매수(롱)'])),
             (f"인버스 추가 {row['추가 매수(인버스)']}", tone(row['추가 매수(인버스)']))]
    badge = '현재 시황 기준' if current else '이전 시황 · 참고' if last else None
    return sv.card_html(f"{row['국면']} · 오늘 {row['오늘 흐름']}",
                        [x for x in (('청산', rest), ('인버스', row.get('인버스 청산')), ('장 마감', row['장 마감'])) if x[1]], color, icon, badge,
                        current, chips=chips, wide=True)


def matching(rows, regime, tone):
    """Index of the row for this regime and today's tone (ignores data freshness), or None."""
    return next((i for i, r in enumerate(rows) if r['국면'] == regime and tone in r['오늘 흐름'].split('·')), None)


def checklist_html(items, color='#22c55e'):
    return (f'<ol class="sv-check" style="--c:{color}">' + ''.join(f'<li>{escape(text)}</li>' for text in items) + '</ol>')


def route_order(rules):
    """The order the runner checks the entry routes in a cycle (nh_paper_isolated: v2 → rebound → slot_entry → Jev pick)."""
    slots = ['신규주 슬롯'] * bool(rules.get('ipo_slot_on')) + ['관심종목 강도 슬롯'] * bool(rules.get('strength_slot_on'))
    return ' → '.join(['낙폭과대 선별 v2', '반등', *slots, 'Jev', '관심종목 강도', '신규주'])


def route_cards(rules):
    """Jev's entry routes, from saved settings rather than execution status (the intraday vibe routes ended 2026-11-17)."""
    strength = bool(rules['strength_top_n'])
    ipo = bool(rules['ipo_days'])
    # strategy slots (user 2026-11-17): checked every cycle before Jev's pick, each with its own limits
    strength_when = (f"Jev 판단보다 먼저 (전략 슬롯 · 동시 {int(rules.get('strength_slot_max', 2))} · 하루 {int(rules.get('strength_slot_daily', 3))}종목)"
                     if rules.get('strength_slot_on') else 'Jev가 대기할 때')
    ipo_when = ('Jev 판단보다 먼저 (전략 슬롯 · 인버스 보유 중에도)' if rules.get('ipo_slot_on') else '위 두 경로에 후보가 없을 때')
    time_stop = int(rules.get('ipo_time_stop_min', 0))
    oversold = bool(rules.get('oversold_on'))
    v2 = bool(rules.get('oversold_v2_on'))
    from session_times import hhmm
    cards = [
        ('0 · 낙폭과대 선별 v2', [('언제', f"시장 폭락일 {hhmm(rules.get('oversold_v2_from_hhmm', 1430))}~{hhmm(rules.get('oversold_v2_until_hhmm', 1455))} (가장 먼저 확인)"),
                             ('조건', f"지수 −{rules.get('oversold_v2_index_drop_pct', 8):g}% 또는 직전 5일 −{rules.get('oversold_v2_prior5_pct', 5):g}% 뒤 "
                                     f"−{rules.get('oversold_v2_capit_drop_pct', 5):g}% · 전날 나스닥 상승 아님 · 시장 대비 −{rules.get('oversold_v2_rel_max_pct', 4):g}%p 이내 · "
                                     f"리서치 거부권(Jev WAIT {rules.get('oversold_v2_veto_conf', 0.7):g}+)"
                                     if v2 else '꺼짐'),
                             ('크기', f"분할 1회의 {rules.get('oversold_v2_size_pct', 100):g}% "
                                     + ('(첫날 절반 · 다음 날 더 싸면 절반)' if rules.get('oversold_v2_add_on', 1) else '(한 번에)')
                                     + f" · 하루 {int(rules.get('oversold_v2_max_new', 3))}종목"),
                             ('청산', f"+{rules.get('oversold_v2_half_tp_pct', 8):g}% 절반 · +{rules.get('oversold_v2_tp_pct', 15):g}% 나머지 · "
                                     f"{int(rules.get('oversold_v2_hold_days', 3))}거래일 · 평균가 −{rules.get('oversold_v2_stop_pct', 2):g}% 손절 뒤 저점 재매수")], '#be185d', 'crisis_alert', v2),
        ('0 · 낙폭과대 반등', [('언제', '감시 종목이 평소보다 훨씬 크게 빠졌다 멈출 때 (가장 먼저 확인)'),
                          ('조건', f"개별주 −{rules['oversold_stock_drop_pct']:g}% · ETF −{rules['oversold_etf_drop_pct']:g}% · 변동폭 "
                                  f"{rules['oversold_atr_mult']:g}배 · {int(rules['oversold_calm_min'])}분 저가 유지 · Jev BUY" if oversold else '꺼짐'),
                          ('크기', f"분할 1회의 {rules['oversold_size_pct']:g}%"),
                          ('청산', f"+{rules['oversold_tp_pct']:g}% 또는 낙폭 {rules['oversold_recover_pct']:g}% 회복 · 저가 −{rules['oversold_stop_pct']:g}% · "
                                  f"{int(rules['oversold_hold_days'])}거래일")], '#ec4899', 'south_east', oversold),
        ('1 · Jev 판단', [('언제', 'Jev가 한 종목을 고를 때'), ('조건', f"신뢰도 ≥ {rules['entry_confidence']:.2f} + 지수 방향·거래량 확인"),
                         ('크기', '분할 1회 매수 금액'), ('청산', '국면별 방식(위 카드)')], '#a855f7', 'psychology', True),
        ('2 · 관심종목 강도', [('언제', strength_when),
                           ('조건', f"Jev 선정 상위 {int(rules['strength_top_n'])}위 · 확률 ≥ {rules['strength_min_prob']:g} · "
                                   f"+{rules['strength_change_min']:g}~{rules['strength_change_max']:g}% · 분봉·거래량" if strength else '꺼짐'),
                           ('크기', '분할 1회 매수 금액'), ('청산', '국면별 방식(위 카드)')], '#06b6d4', 'local_fire_department', strength),
        ('3 · 신규주', [('언제', ipo_when),
                      ('조건', f"상장 후 {int(rules['ipo_days'])}거래일 · 기준가·시가 위 · 고가 {rules['ipo_near_high_pct']:g}% 안" if ipo else '꺼짐'),
                      ('크기', f"분할 1회의 {rules['ipo_size_pct']:g}% · 동시 {int(rules.get('ipo_max_positions', 1))}종목 · 하루 {int(rules.get('ipo_daily_max', 2))}종목"),
                      ('청산', f"−{rules['ipo_stop_pct']:g}% · +{rules['ipo_half_pct']:g}% 절반 · 고점 −{rules['ipo_trail_pct']:g}%"
                              + (f" · 산 뒤 {time_stop}분 지나면 정리" if time_stop else '') + ' · 당일 정리')],
         '#84cc16', 'new_releases', ipo),
    ]
    return cards
