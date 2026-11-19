"""장기투자 판단 근거 가이드 (user request 2026-11-18): what the long-term engine looks at when it "reads the market" — every
data source, when it is read, which decision uses it and whether that use is a rule (it moves an order) or a reference
(it only feeds the LLM story check or the reports) — with diagrams, the regime rules, each master's inputs, the 적립식
거장 판단 and 매수 시각 자동, what it does NOT look at (unlike Jev), and today's actual values. Read-only."""
from __future__ import annotations

import streamlit as st

import vibe_longterm as L

RULE, VIA_STORY, REF = '규칙 · 주문에 직접', '스토리 경유 · 위성 비중', '참고 · 리포트·표시'
SOURCES = [  # (데이터, 출처, 언제 읽나, 어디에 쓰나, 매매에 쓰는 방식)
    ('나스닥100 지수(^NDX) 10년 일봉', 'Yahoo Finance', '평일 08:45 아침 판단',
     '국면 판정(252일 고점 대비 낙폭 · 200일선 대비 · 200일선 기울기), 저수지 단계(−20·−30%), 여운봉 매매법 전고점(10년), '
     '적립식 하락일(전날 하락)·급락일(하루 등락 −3%)', RULE),
    ('VIX(^VIX) 2년 일봉', 'Yahoo Finance', '08:45', '국면 판정(투매 VIX ≥ 35 · 과열 VIX ≤ 14), 윌리엄스 급락 반등(VIX ≥ 25), 적립식 급락일', RULE),
    ('테마 대용 미국 ETF(SOXX·QTUM·UFO) 2년 일봉', 'Yahoo Finance', '08:45',
     '위성(반도체·양자·우주)의 200일선 대비·기울기·아래 머문 일수 → 윌리엄스 추세(위성 줄이기·되돌림), 리포트', RULE),
    ('S&P500(^GSPC)·코스피(^KS11) 10년 일봉', 'Yahoo Finance', '08:45', '여운봉 저수지 매매법에서 그 지수를 골랐을 때의 전고점 대비 낙폭', RULE),
    ('KRX 상장 ETF 6종 2년 일봉(.KS)', 'Yahoo Finance', '08:45', '타점 지도의 가격(지수 낙폭을 ETF 가격으로 옮김), 전일 종가(적립식 매수 시각 자동)', RULE),
    ('NH 실시간 호가', 'NH 나무 PLUG', '09:40 주문 · 적립식 확인 때', '주문 가격(매수 매도호가), 적립식 매수 시각 자동(지금 값 ≤ 전일 종가?)', RULE),
    ('LLM 테마 스토리 판정', 'vibe-trading 에이전트(Claude)', '08:45', '반도체·양자·우주 각각 유지 · 주의 · 훼손 + 기울기 → 린치 모듈이 위성 비중 조정', VIA_STORY),
    ('장기 관점 뉴스(최근 36시간)', 'Google News → Bing → 네이버(막히면 다음)', '08:45 전 하루 한 번',
     '주제 6개: 연준 · 미국 거시(증시·물가·고용·국채) · 반도체 · 반도체 정책·지정학(수출 규제·관세·대만) · 양자 · 우주 → 스토리 판정의 근거 자료',
     VIA_STORY),
    ('FRED 금리·신용 4종 + 발표 일정, 연준 FOMC 일정', 'FRED API · 연준 일정 페이지', '08:45',
     '10년−2년 금리차 · 미 10년 금리 · 10년 실질금리 · 하이일드 스프레드, 30일 안 CPI·고용·GDP·PCE·PPI·FOMC → 스토리 판정 · 리포트 2절', VIA_STORY),
    ('Jev 매매일지 요약 · 장전 브리프', '이 시스템(Jev 엔진)', '08:45', '스토리 판정의 배경 자료(다른 엔진의 하루 시각)', VIA_STORY),
    ('원/달러(KRW=X)', 'Yahoo Finance', '08:45', '시황 카드 표시 · 리포트(원화 낙폭과 지수 낙폭 차이 설명)', REF),
    ('이번 달 계절성(최근 10년 같은 달 평균)', '나스닥100 일봉에서 계산', '08:45', '시황 카드 · 리포트 참고(규칙 아님)', REF),
]
NOT_USED = [
    ('코스피·코스닥 등 국내 지수의 장중 흐름', 'Jev는 봄', '미국 지수 ETF 장기 운용이라 미국 지수로 판단(코스피는 여운봉 매매법에서 코스피를 고를 때 전고점만)'),
    ('거래량 · 거래대금 · 순위', 'Jev는 봄', '장기 보유 판단에 하루 거래 활동을 쓰지 않음'),
    ('투자자별 수급(외국인·기관) · 프로그램 매매', 'Jev는 봄', '쓰지 않음'),
    ('분봉 차트 · 장중 VWAP', 'Jev는 봄', '쓰지 않음(적립식 매수 시각 자동만 지금 호가와 전일 종가를 비교)'),
    ('지정학·전쟁·정치 뉴스(일반 주제)', 'Jev 매매일지·Jev 뉴스는 봄',
     '시장 전체에 큰 충격은 VIX·낙폭으로 국면 규칙에 이미 들어오므로 일반 주제는 따로 모으지 않음. 반도체 이야기를 바꾸는 수출 규제·관세·대만은 별도 주제로 읽음'),
    ('개별 종목 실적·애널리스트 의견', '미국 종목 아이디어 탭(참고)', '엔진 규칙은 쓰지 않음'),
]
FEATURES = [  # (기능, 보는 데이터, 판단 방법)
    ('장기 국면', '나스닥100 낙폭(252일 고점) · 200일선 대비·기울기 · VIX',
     '투매 → 약세 → 조정 → 회복 → 과열 → 강세 안정 순서로 먼저 맞는 국면을 고르고, 같은 새 국면이 확정 일수(기본 10거래일, 투매 2)만큼 이어져야 바꿉니다.'),
    ('국면 추천 등급(강력 추천·추천·비권장)', '확정 국면', '국면마다 정해 둔 추천 모듈 조합(국면 표)과 재현 결과로 매깁니다. 안내일 뿐 자동으로 바꾸지 않습니다.'),
    ('기준 비중', '없음(설정값)', '저장한 기준 비중을 들고 갑니다. 시황을 보지 않습니다.'),
    ('달리오 · 월 밴드', '보유 비중 · 오늘 목표', '매월 첫 거래일, 목표에서 밴드 밖으로 벗어난 종목이 있으면 전체를 목표로 되돌립니다.'),
    ('저수지 분할(일반)', '나스닥100 고점 대비 낙폭', '−20%·−30%에 처음 닿으면 단계마다 저수지 7.5%를 코어로 옮깁니다. 신고가면 단계 재설정.'),
    ('막스 · 시계추', '확정 국면(과열·투매)', '과열이면 코어 5%를 저수지로, 투매면 저수지 단계를 −15%·−25%로 앞당깁니다.'),
    ('윌리엄스 · 추세', '테마 대용 ETF 200일선', '−2% 아래 5거래일이면 그 위성을 절반으로, +2% 위로 회복하면 되돌립니다.'),
    ('윌리엄스 · 급락 반등', '나스닥100 하루 등락 · VIX', '하루 −3% 이하이고 VIX ≥ 25이면 저수지 5%를 나스닥100으로 옮기고 10거래일 뒤 되돌립니다.'),
    ('린치 · 테마 스토리', 'LLM 판정(뉴스·거시·Jev 요약·지표를 읽음)', '주의면 위성 절반, 훼손이면 0, 기울기 ±25%. LLM이 뉴스를 읽지만 주문은 이 판정을 거쳐서만 바뀝니다.'),
    ('버핏 · 저회전', '이번 달 매도 금액', '손절하지 않고, 저수지를 뺀 매도를 한 달 순자산의 10%까지만 합니다.'),
    ('여운봉 저수지 매매법', '고른 지수의 전고점(10년) 대비 낙폭 · 보유 비율', '−20·−30·−40%에서 저수지 40·40·20%를 위험 70 : 안전 30으로 사고, 보유 뒤 매월·분기 첫 확인일과 5%p 이탈 즉시 70:30으로 되돌립니다.'),
    ('적립식 · 거장 판단', '확정 국면 · 나스닥100 낙폭 · 급락일(하루 등락·VIX)',
     '매 거래일 확인해 매수 빈도 기간에 한 번까지 삽니다. 금액은 기본 금액 × 거장의 국면 배수(0.5~1.5배, 0·전액은 없음)이고, '
     '하락일 우선 거장(막스·여운봉·윌리엄스)은 그 기간의 하락일을 기다리며 윌리엄스는 급락일 1.5배입니다.'),
    ('적립식 · 시황 자동(그림자)', '확정 국면', '국면마다 정해 둔 거장(투매 여운봉 · 약세 보글·버핏 · 조정 막스 · 회복 달리오 · 과열 막스 · 강세 보글·버핏)으로 그림자 장부만 적립합니다.'),
    ('적립식 · 매수 시각 자동', 'NH 지금 호가 · KRX 전일 종가', '09:40부터 5분마다 보고, 사려는 주식 ETF가 전일 종가 이하인 첫 순간에 삽니다. 없으면 14:20.'),
]


def flow_dot():
    return '''digraph G {
  rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fillcolor="#f1f5f9", fontname="Pretendard", fontsize=11];
  subgraph cluster_rule { label="규칙 입력 (숫자 그대로)"; style="rounded"; color="#22c55e"; fontname="Pretendard";
    ndx [label="나스닥100 · VIX\\n(Yahoo 일봉)"]; proxy [label="테마 대용 ETF\\nSOXX·QTUM·UFO"]; ath [label="전고점 10년\\nNDX·S&P500·코스피"];
    krx [label="KRX ETF 일봉\\nNH 실시간 호가"]; }
  subgraph cluster_ref { label="참고 입력 (LLM이 읽음)"; style="rounded,dashed"; color="#a855f7"; fontname="Pretendard";
    news [label="장기 뉴스 36시간\\n연준·거시·반도체(+수출규제·관세·대만)·양자·우주"]; macro [label="FRED 금리·스프레드\\n발표·FOMC 일정"]; jev [label="Jev 일지 요약\\n장전 브리프"]; }
  regime [label="국면 판정\\n(확정 일수)"]; story [label="테마 스토리 판정\\n(LLM: 유지·주의·훼손)"];
  modules [label="거장 모듈\\n오늘 목표 비중"]; dca [label="적립식\\n거장 판단·매수 시각"]; yeo [label="여운봉 매매법\\n단계·70:30"];
  orders [label="09:40 주문\\n(가상 · 실계좌)", fillcolor="#ede9fe"];
  ndx -> regime; ndx -> modules; proxy -> modules; regime -> modules; regime -> dca; ndx -> dca; ath -> yeo; krx -> orders; krx -> dca;
  news -> story; macro -> story; jev -> story; ndx -> story [style=dotted]; story -> modules [label="위성 비중"];
  modules -> orders; dca -> orders; yeo -> orders;
}'''


import guide_ui as GU  # noqa: E402 - the shared pop-up template


def data_flow():
    return GU.workflow('장기 엔진이 보는 데이터 · 판단 · 주문', [('rule', '규칙 입력 · 숫자 그대로'), ('ref', '참고 입력 · LLM이 읽음'),
                                                      ('judge', '판단 (08:45)'), ('act', '주문')],
        [('ndx', 'rule', 0, '나스닥100 · VIX', 'Yahoo 일봉', 'external'), ('proxy', 'rule', 1, '테마 대용 ETF', 'SOXX·QTUM·UFO', 'external'),
         ('ath', 'rule', 2, '전고점 10년', 'NDX·S&P500·코스피', 'external'), ('krx', 'rule', 4, 'KRX ETF 일봉', 'NH 실시간 호가', 'external'),
         ('news', 'ref', 0, '장기 뉴스 36시간', '연준·거시·반도체·정책·양자·우주', 'cloud'), ('macro', 'ref', 1, 'FRED 금리·스프레드', '발표·FOMC 일정', 'cloud'),
         ('jev', 'ref', 2, 'Jev 일지 요약', '장전 브리프', 'cloud'),
         ('regime', 'judge', 0, '국면 판정', '확정 일수', 'security'), ('story', 'judge', 2, '테마 스토리', 'LLM: 유지·주의·훼손'),
         ('modules', 'judge', 3, '거장 모듈', '오늘 목표 비중'), ('dca', 'judge', 4, '적립식', '거장 판단·매수 시각'),
         ('yeo', 'judge', 5, '여운봉 매매법', '전고점 단계·70:30'), ('orders', 'act', 4, '09:40 주문', '가상 계좌 · 실계좌', 'messagebus')],
        [('ndx', 'regime', '낙폭·200일선·VIX', 'main'), ('regime', 'modules', '국면', 'main'), ('proxy', 'modules', '위성 추세'),
         ('news', 'story', None, 'async'), ('macro', 'story', None, 'async'), ('jev', 'story', None, 'async'),
         ('story', 'modules', '위성 비중'), ('regime', 'dca', '국면 비율'), ('ath', 'yeo', '단계'), ('krx', 'dca', '어제 종가'),
         ('modules', 'orders', None, 'main'), ('dca', 'orders'), ('yeo', 'orders'), ('krx', 'orders', '호가')],
        main=['ndx', 'regime', 'modules', 'orders'])

table = GU.table


def _overview():
    GU.lead('장기 엔진은 **매일 아침 08:45** 데이터를 읽어 **국면**과 **테마 스토리**를 판정하고, 켜진 거장 모듈이 그 결과로 오늘 목표 비중을 정합니다. '
            '숫자 데이터는 규칙에 바로 들어가고, 뉴스·금리는 LLM 테마 스토리 판정을 거쳐서만 비중에 닿습니다.')
    GU.flow('lt-inputs', data_flow(), flow_dot(), '실선 = 숫자가 그대로 규칙에 들어가는 입력 · 점선 = LLM이 읽는 참고 입력(뉴스가 직접 주문을 만들지 않음). 초록 상자 = 숫자가 그대로 규칙에 들어가는 입력 · 보라 점선 상자 = LLM이 읽는 참고 입력(뉴스가 직접 주문을 만들지 않음)')
    GU.note('Jev(장중 엔진)와 달리 장기 엔진은 **하루 한 번 일봉 기준**으로 판단합니다. 국내 지수 장중 흐름·거래량·거래대금·수급·분봉은 보지 않습니다'
            '("보지 않는 것" 탭).')


def _sources():
    GU.section('보는 데이터 전체', '데이터마다 어디서·언제 읽어 무엇에 쓰는지와, 매매에 쓰는 방식(규칙·스토리 경유·참고)')
    table(SOURCES, ('데이터', '출처', '언제', '어디에 쓰나', '매매에 쓰는 방식'))
    GU.caption(f'"{RULE}": 숫자가 규칙에 들어가 목표 비중·주문을 바꿉니다. "{VIA_STORY}": LLM 테마 스토리 판정의 근거로만 쓰이고, 그 판정(유지·주의·훼손)이 '
               f'린치 모듈을 통해 위성 비중을 바꿉니다. "{REF}": 화면·리포트에만 보입니다.')


def _regime():
    GU.section('국면 판정 규칙', '위에서부터 먼저 맞는 국면이 오늘의 "원시 국면"이고, 같은 원시 국면이 확정 일수만큼 이어지면 공식 국면이 바뀝니다.')
    table([(f'{i}번째', L.REGIMES[k][0], L.REGIMES[k][1], ', '.join(f'{L.MODULES[m][0]} {L.MODULES[m][1]}' for m in L.REGIMES[k][2]))
           for i, k in enumerate(L.REGIMES, 1)], ('판정 순서', '국면', '조건', '추천 모듈(기준 비중 외)'))
    days = L.load_settings(L.ROOT)['tuning']['confirm_days']
    GU.caption(f'확정 일수: 지금 {days}거래일(투매는 {L.CONFIRM_DAYS["panic"]}거래일) · 시장 → 시황·국면에서 바꿉니다. '
               '낙폭은 나스닥100 252거래일 고점 대비, 200일선 대비는 종가 ÷ 200일 평균 − 1입니다.')


def _features():
    GU.section('기능별로 무엇을 보고 어떻게 판단하나')
    table(FEATURES, ('기능', '보는 데이터', '판단 방법'))
    GU.caption('숫자(단계 낙폭·비율·일수)는 기본값입니다. 장기투자 → 전략 → 각 거장 카드의 "수치 조정"에서 바꿀 수 있습니다.')


def _not_used():
    GU.lead('장기 엔진은 몇 달~몇 년 보유를 정하는 엔진이라 **하루 거래 활동을 보지 않습니다.** 아래는 Jev는 보지만 장기 엔진 규칙은 쓰지 않는 것입니다.',
            '보지 않는 것')
    table(NOT_USED, ('데이터', '어디서 보나', '장기 엔진에서'))


def _today():
    state = L.read_state(L.ROOT, ('plan', 'story'))
    plan, story = state.get('plan') or {}, state.get('story') or {}
    ind = plan.get('indicators') or {}
    if not ind:
        GU.note('첫 아침 판단(평일 08:45) 뒤에 오늘 값이 보입니다.')
        return
    GU.section('오늘 아침 판단에 쓴 실제 값')
    pct = lambda v: '—' if v is None else f'{v * 100:+.1f}%'.replace('-', '−')  # noqa: E731
    rows = [('판단 시각', (plan.get('time') or '')[:16].replace('T', ' '), f"미국 종가 기준일 {ind.get('as_of', '—')} · {ind.get('source', 'Yahoo')}"),
            ('확정 국면', L.REGIMES.get(plan.get('regime'), ('—',))[0], '공식 국면(확정 일수 반영)'),
            ('나스닥100 낙폭(252일 고점)', pct(ind.get('dd')), '−10% 이하 조정 · −20% 이하 약세 기준'),
            ('200일선 대비 · 기울기', f"{pct(ind.get('gap'))} · {pct(ind.get('slope'))}", '200일선 아래면 조정 이상'),
            ('VIX', f"{ind.get('vix', 0):.1f}", '35 이상 투매(낙폭 −15%와 함께) · 14 이하 과열(200일선 ×1.15 위와 함께)'),
            ('나스닥100 하루 등락', pct(ind.get('day_ret')), '−3% 이하 + VIX 25 이상이면 급락일'),
            *((('시장 온도(막스식 공포·탐욕, 참고)', f"{temp['score']:.0f} · {temp['label']}", '0 극단적 공포 ~ 100 극단적 탐욕 · 규칙·주문은 읽지 않음'),)
              if (temp := L.temperature(ind, plan.get('macro'))) else ()),
            ('원/달러', f"{ind.get('usdkrw', 0):,.0f}", '표시·리포트')]
    for k in L.SATS:
        t = (ind.get('sats') or {}).get(k) or {}
        rows.append((f'{L.ASSETS[k][1]} 대용 추세', f"200일선 {pct(t.get('gap'))} · 아래 {t.get('below_days', '—')}일", '윌리엄스 추세'))
    for index, a in (ind.get('ath') or {}).items():
        if isinstance(a, dict):
            rows.append((f"{index.upper()} 전고점 대비", pct(a.get('dd')), f"전고점 {a.get('peak', 0):,.0f}({a.get('peak_day', '—')}) · 여운봉 매매법"))
    table(rows, ('항목', '오늘 값', '뜻'))
    stories = story.get('stories') or {}
    if stories:
        GU.section('오늘 테마 스토리 판정')
        table([(L.ASSETS[k][1], L.STORY_LABELS.get(v.get('status'), v.get('status')), v.get('reason', '')) for k, v in stories.items() if k in L.ASSETS],
              ('테마', '판정', '이유'))
        if story.get('market_view'):
            GU.note(f"LLM 시각: {story['market_view']}")
    macro = plan.get('macro') or {}
    if macro.get('series'):
        GU.caption('거시 배경: ' + ' · '.join(f"{s['label']} {s['value']:.2f}{s['unit']}" for s in macro['series'].values()))


PARTS = (('① 한눈에 보기', _overview), ('② 데이터 전체', _sources), ('③ 국면 판정', _regime), ('④ 기능별 판단', _features),
         ('⑤ 보지 않는 것', _not_used), ('⑥ 오늘 실제 값', _today))


def open_guide(focus=None):
    GU.open('장기투자 판단 근거 · 무엇을 보고 사나', PARTS, ':material/query_stats:', focus)


def button(key, focus=None, label='판단 근거 보기'):
    """'판단 근거 보기': the pop-up above, opened on the part named by focus (e.g. '국면 판정', '기능별 판단')."""
    if st.button(label, key=key, icon=':material/query_stats:', type='tertiary',
                 help='장기 엔진이 어떤 데이터(지수·VIX·테마 추세·뉴스·금리 …)를 언제 읽어 어떻게 판단하는지 도식과 함께 봅니다.'):
        open_guide(focus)


# ---------- 적립식 매수 시각 · 거장 자동(장중 시황) (user request 2026-11-18) ----------

def time_dot():
    import longterm_dca as D
    return f'''digraph G {{
  rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fillcolor="#f1f5f9", fontname="Pretendard", fontsize=11];
  close [label="08:45 아침 판단\\n어제 KRX 종가 기록\\n(ETF 6종 · Yahoo .KS 일봉)"];
  look [label="{D.TIMES[0]}부터 {D.AUTO_CHECK_MIN}분마다\\nNH 지금 호가 읽기\\n(사려는 주식 ETF만)"];
  test [label="지금 매수호가 ≤ 어제 종가?\\n(사려는 ETF 중 하나라도)", shape=diamond, fillcolor="#fef3c7"];
  buy [label="지금 매수\\n그날 적립 금액을 목표 비중대로\\n1주 단위로 나눠 주문", fillcolor="#dcfce7"];
  wait [label="기다림\\n({D.AUTO_CHECK_MIN}분 뒤 다시)"];
  last [label="{D.TIMES[1]} 마지막 확인\\n조건과 상관없이 매수", fillcolor="#dcfce7"];
  close -> test [style=dotted, label="기준값"]; look -> test; test -> buy [label="예"]; test -> wait [label="아니오(오르는 중)"];
  wait -> look; wait -> last [style=dashed, label="{D.TIMES[1]}까지 없으면"];
}}'''


def time_flow():
    import longterm_dca as D
    return GU.workflow('적립식 매수 시각 · 거장 자동', [('prep', '아침 준비'), ('loop', '장중 확인'), ('done', '매수')],
        [('close', 'prep', 0, '08:45 아침 판단', '어제 KRX 종가 기록', 'database'),
         ('look', 'loop', 1, f'{D.TIMES[0]}부터 {D.AUTO_CHECK_MIN}분마다', 'NH 지금 호가 읽기', 'external'),
         ('test', 'loop', 2, '매수호가 ≤ 어제 종가?', '사려는 ETF 중 하나라도', 'security'),
         ('wait', 'loop', 3, '기다림', f'{D.AUTO_CHECK_MIN}분 뒤 다시', 'messagebus'),
         ('buy', 'done', 3, '지금 매수', '목표 비중대로 1주 단위'), ('last', 'done', 4, f'{D.TIMES[1]} 마지막 확인', '조건과 상관없이 매수')],
        [('close', 'test', '기준값', 'async'), ('look', 'test', None, 'main'), ('test', 'buy', '예', 'main'), ('test', 'wait', '아니오(오르는 중)'),
         ('wait', 'look', '다시', 'return'), ('wait', 'last', f'{D.TIMES[1]}까지 없으면', 'branch')],
        main=['look', 'test', 'buy'])


def day_flow():
    import longterm_dca as D
    return GU.workflow('적립식 하루 흐름', [('plan', '아침'), ('decide', '적립 판단'), ('time', '매수 시각'), ('exec', '주문·기록')],
        [('a', 'plan', 0, '08:45 아침 판단', '국면·목표 비중·어제 종가', 'database'),
         ('b', 'decide', 1, '오늘 살 차례?', '정한 날·거장 판단·하락일', 'security'), ('skip', 'plan', 2, '오늘은 적립 없음', '', 'external'),
         ('c', 'decide', 2, '얼마나 · 무엇을', '금액/수량 · 목표 비중/고른 종목'), ('d', 'decide', 3, '언제(매수 시각)', '', 'security'),
         ('fixed', 'decide', 4, '정한 시각에 바로'), ('auto', 'time', 4, '거장 자동', f'{D.TIMES[0]}~{D.TIMES[1]} · 어제 종가와 비교'),
         ('e', 'exec', 5, '주문 → 기록', '가상 체결 / 실계좌 보호 경로 · 근거·Discord', 'messagebus')],
        [('a', 'b', None, 'main'), ('b', 'skip', '아니오', 'branch'), ('b', 'c', '예', 'main'), ('c', 'd', None, 'main'),
         ('d', 'fixed', '정한 시각', 'main'), ('d', 'auto', '거장 자동'), ('fixed', 'e', None, 'main'), ('auto', 'e')],
        main=['a', 'b', 'c', 'd', 'fixed', 'e'])


def _time_how():
    import longterm_dca as D
    GU.lead(f'"거장 자동(장중 시황)"은 그날 적립식이 살 차례일 때 **언제 살지**만 정합니다. 얼마나·무엇을은 투자 시점·크기·종목 설정을 따릅니다. '
            f'원칙: **오르는 중에는 비싸게 사지 않고 어제 종가 이하로 내려온 순간에 산다. 그런 때가 없으면 {D.TIMES[1]}에 산다.**', '원칙')
    GU.flow('lt-time', time_flow(), time_dot(), f'판단은 방패 아이콘, 굵은 길은 내려온 순간 사는 경로입니다. {D.TIMES[0]}부터 {D.AUTO_CHECK_MIN}분마다 같은 판단을 반복합니다.')
    GU.section('순서')
    table([('1', '08:45', '아침 판단이 ETF 6종의 어제 KRX 종가를 기록합니다(Yahoo .KS 일봉, 엔진 판단 기록 plan.indicators.krx_close).'),
           ('2', f'{D.TIMES[0]}~', f'적립식이 살 차례인 날만, {D.AUTO_CHECK_MIN}분마다 NH 실시간 호가를 읽습니다(시세 조회 한도를 아끼려고 매분이 아님).'),
           ('3', '확인 때마다', '오늘 살 주식 ETF(목표 비중 > 0 또는 고른 종목, 저수지 KOFR 제외) 중 하나라도 지금 매수호가 ≤ 어제 종가이면 바로 삽니다.'),
           ('4', '아니면', f'기다렸다가 {D.AUTO_CHECK_MIN}분 뒤 다시 봅니다. 그 사이 주문은 없습니다.'),
           ('5', D.TIMES[1], '마지막 확인: 조건과 상관없이 삽니다(적립을 빼먹지 않기).'),
           ('6', '하루 한 번', '산 뒤에는 그날 다시 사지 않습니다. 주문은 가상 계좌는 가상 체결, 실계좌는 실계좌 보호 경로(실주문 스위치가 켜졌을 때)로 나갑니다.')],
          ('순서', '시각', '하는 일'))


def _time_data():
    import longterm_dca as D
    GU.section('보는 데이터')
    table([('어제 KRX 종가', 'Yahoo Finance .KS 일봉(KODEX 미국나스닥100 등 ETF 6종)', '08:45 하루 한 번', '비교 기준값'),
           ('지금 매수호가', 'NH 나무 PLUG 실시간 호가', f'{D.TIMES[0]}부터 {D.AUTO_CHECK_MIN}분마다', '지금 사면 내는 값'),
           ('오늘 살 종목', '엔진 오늘 목표 비중 또는 적립식 "적립할 종목"', '그때그때', '비교할 ETF 목록(저수지 KOFR 제외)'),
           ('시각', '컴퓨터 시계(한국 시간)', '확인 때마다', f'{D.TIMES[1]} 마지막 확인')],
          ('데이터', '출처', '언제', '쓰임'))
    GU.section('뉴스·국면은 어디서 반영되나', '이 옵션은 이미 정해진 오늘의 매수를 몇 시에 낼지만 정하므로 "어제보다 싸게 살 수 있나"만 봅니다. '
               '뉴스·국면은 그 전 단계에서 이미 반영됩니다.')
    table([('① 투자 시점(거장 판단일 때)', '오늘 살지 말지', '국면(나스닥100 낙폭·200일선·VIX), 급락일'),
           ('② 얼마나 · 무엇을', '금액·종목', '국면별 비율, 엔진 목표 비중 — 테마 스토리(LLM이 장기 뉴스·금리·반도체 수출규제 등을 읽고 판정) 반영'),
           ('③ 매수 시각(이 옵션)', '몇 시에', '지금 호가 vs 어제 종가')], ('단계', '정하는 것', '보는 것'))
    GU.caption('같은 날 ③에서 뉴스·국면을 다시 보면 ①·②의 결정을 뒤집게 되므로 넣지 않았습니다. 장기 거장 대부분(보글·버핏·린치)도 '
               '뉴스로 매매 시점을 잡지 말라는 쪽이고, 시장 환경을 보는 막스·달리오의 관점은 ①의 국면으로 반영됩니다.')


def _time_example():
    import longterm_dca as D
    GU.section('예시', '어제 종가: KODEX 미국나스닥100 24,000원, KODEX 미국S&P500 22,000원. 오늘 둘 다 삽니다.')
    table([('09:40', '24,150 / 22,080', '둘 다 어제보다 비쌈', '기다림'),
           ('09:45', '24,050 / 22,010', '둘 다 비쌈', '기다림'),
           ('09:50', '23,990 / 22,030', '나스닥100이 어제 종가 이하', '**지금 둘 다 삼**(그날 적립 금액을 목표 비중대로)'),
           (D.TIMES[1], '(내려온 적이 없었다면)', '마지막 확인', '조건 없이 삼')],
          ('시각', '매수호가(나스닥100 / S&P500)', '판단', '행동'))
    GU.note('**정한 시각과 다른 점** · 정한 시각은 그 시각 가격이 얼마든 삽니다. 거장 자동은 오른 날 아침의 높은 가격을 피하고, 내린 날엔 일찍 삽니다. '
                '하루 안의 값 차이만 줄일 뿐 수익을 보장하지 않습니다(재현 전).')
    GU.note('**예외** · 어제 종가 기록이 없으면(아침 판단 실패·첫날) 기다리지 않고 바로 삽니다. 실계좌는 기다리는 동안 실행 기록에 '
                '"적립식: 매수 시각 자동 — 전일 종가 이하로 내려올 때까지 대기"가 남습니다.')


def _time_today():
    import longterm_dca as D
    state = L.read_state(L.ROOT, ('plan',))
    close = ((state.get('plan') or {}).get('indicators') or {}).get('krx_close') or {}
    if not close:
        GU.note('아직 어제 종가 기록이 없습니다. 다음 아침 판단(평일 08:45)부터 기록됩니다. 그 전에는 기다리지 않고 바로 삽니다.')
        return
    table([(f'{L.asset_name(k)} ({L.code_of(k)})', f'{v:,.0f}원', '이 값 이하로 내려오면 매수') for k, v in close.items() if k != 'RES'],
          ('ETF', '어제 종가(기준값)', '판단'))
    GU.caption(f"기준일: 아침 판단 {((state.get('plan') or {}).get('time') or '')[:16].replace('T', ' ')} · 마지막 확인 {D.TIMES[1]}")


def day_dot():
    """The whole 적립식 day with the buy-time choice: from the morning decision to the order, on both books."""
    import longterm_dca as D
    return f'''digraph G {{
  rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", fillcolor="#f1f5f9", fontname="Pretendard", fontsize=11];
  a [label="08:45 아침 판단\\n국면 · 오늘 목표 비중\\n어제 KRX 종가 기록"];
  b [label="오늘 살 차례?\\n(정한 날 / 거장 판단 / 하락일)", shape=diamond, fillcolor="#fef3c7"];
  skip [label="오늘은 적립 없음"];
  c [label="얼마나 · 무엇을\\n금액 또는 수량 · 목표 비중 또는 고른 종목"];
  d [label="언제(매수 시각)", shape=diamond, fillcolor="#fef3c7"];
  fixed [label="정한 시각에 바로"]; auto [label="거장 자동\\n{D.TIMES[0]}~{D.TIMES[1]}\\n{D.AUTO_CHECK_MIN}분마다 어제 종가와 비교"];
  e [label="주문\\n가상 계좌: 가상 체결\\n실계좌: 보호 경로(실주문 켜짐일 때)", fillcolor="#dcfce7"];
  f [label="기록\\n판단 근거 · 체결 · Discord"];
  a -> b; b -> skip [label="아니오"]; b -> c [label="예"]; c -> d; d -> fixed [label="정한 시각"]; d -> auto [label="거장 자동"];
  fixed -> e; auto -> e; e -> f;
}}'''


def _time_algorithm():
    import longterm_dca as D
    GU.section('하루 전체 흐름', '매수 시각은 "언제"만 정하는 마지막 단계입니다.')
    GU.flow('lt-dca-day', day_flow(), day_dot())
    GU.section('알고리즘', '엔진(가상 계좌)과 실계좌 실행기가 같은 함수(`longterm_dca.start_time`·`auto_look`·`ready`)를 씁니다.')
    st.code(f"""매 확인(엔진 30초 · 실계좌 1분)마다:
  if 지금 < {D.TIMES[0]}:                      아직 시작 전 → 대기
  if 오늘 이미 적립식 확인을 끝냈다:            끝
  if 지금 분이 {D.AUTO_CHECK_MIN}의 배수가 아니고 지금 < {D.TIMES[1]}:  호가를 읽지 않고 대기   # 시세 조회 아끼기
  사려는_ETF = 오늘 살 주식 ETF(목표 비중 > 0 또는 고른 종목) - 저수지(KOFR)
  기준 = 어제 KRX 종가(08:45 기록)
  if 기준이 없다:                              바로 매수                    # 첫날·아침 판단 실패
  if 지금 >= {D.TIMES[1]}:                         매수                         # 마지막 확인
  if 사려는_ETF 중 하나라도 지금 매수호가 <= 기준:  매수                         # 내려온 순간
  else:                                        대기({D.AUTO_CHECK_MIN}분 뒤 다시)
매수 = 그날 적립 금액을 목표 비중대로 나눠 1주 단위 주문(1주 미만은 다음 적립으로 이월), 하루 한 번""", language='text')


def _example_series(cross):
    """An illustrative 5-minute series (not real prices): opens up, dips under yesterday's close when `cross`."""
    import math
    import longterm_dca as D
    base, rows = 24_000, []
    start, end = 9 * 60 + 40, int(D.TIMES[1][:2]) * 60 + int(D.TIMES[1][3:])
    for i, minute in enumerate(range(start, end + 1, 5)):
        drift = 140 - i * (6 if cross else 1.2) + 40 * math.sin(i / 3)
        rows.append({'시각': f'{minute // 60:02d}:{minute % 60:02d}', '가격': round(base + drift), '어제 종가': base})
    return rows


def _time_charts():
    import altair as alt
    import pandas as pd
    import longterm_dca as D
    GU.note('아래 가격은 이해를 돕는 **예시**입니다(실제 시세 아님). 보라 선 = 매수호가, 회색 점선 = 어제 종가(기준값), 초록 점 = 사는 순간.')
    for title, cross in (('A. 장중에 어제 종가 아래로 내려온 날 → 내려온 첫 확인에 매수', True),
                         (f'B. 하루 종일 어제보다 비싼 날 → {D.TIMES[1]} 마지막 확인에 매수', False)):
        rows = _example_series(cross)
        checks = [r for r in rows if int(r['시각'][3:]) % D.AUTO_CHECK_MIN == 0]
        buy = next((r for r in checks if r['가격'] <= r['어제 종가']), rows[-1])
        frame = pd.DataFrame(rows)
        x = alt.X('시각:N', title=None, axis=alt.Axis(labelAngle=-45, values=[r['시각'] for r in rows[::6]]))
        price = alt.Chart(frame).mark_line(color='#6366f1').encode(x=x, y=alt.Y('가격:Q', scale=alt.Scale(zero=False), title='매수호가(원)'))
        close = alt.Chart(frame).mark_line(strokeDash=[6, 4], color='#94a3b8').encode(x=x, y='어제 종가:Q')
        point = alt.Chart(pd.DataFrame([{**buy, '표시': f"매수 {buy['시각']}"}])).mark_point(size=180, filled=True, color='#16a34a').encode(
            x=x, y='가격:Q', tooltip=['표시', '가격'])
        label = alt.Chart(pd.DataFrame([{**buy, '표시': f"매수 {buy['시각']}"}])).mark_text(dy=-16, color='#16a34a', fontWeight='bold').encode(
            x=x, y='가격:Q', text='표시')
        GU.section(title)
        st.altair_chart((price + close + point + label).properties(height=220), width='stretch')
    GU.caption(f'확인은 {D.TIMES[0]}부터 {D.AUTO_CHECK_MIN}분마다입니다. 여러 ETF를 살 때는 그중 하나라도 어제 종가 이하가 되면 그 순간 모두 삽니다.')


TIME_PARTS = (('① 어떻게 정하나', _time_how), ('② 알고리즘·흐름', _time_algorithm), ('③ 차트로 보기', _time_charts),
              ('④ 보는 데이터', _time_data), ('⑤ 예시·예외', _time_example), ('⑥ 오늘 기준값', _time_today))


def time_button(key, container=None):
    """'거장 자동은 어떻게 사나' next to the 적립식 buy-time choice."""
    if (container or st).button('거장 자동은 어떻게 사나', key=key, icon=':material/schedule:', type='tertiary',
                                help='매수 시각 "거장 자동(장중 시황)"이 어떤 데이터를 보고 언제 사는지 도식·예시와 함께 봅니다.'):
        GU.open('적립식 매수 시각 · 거장 자동(장중 시황)', TIME_PARTS, ':material/schedule:')
