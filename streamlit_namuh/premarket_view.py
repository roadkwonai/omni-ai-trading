"""Read-only presentation of the morning research brief."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

import streamlit as st

from report_figures import for_streamlit  # report callouts/highlights in st.markdown

from market_view import number, timestamp
from premarket_template import STATUS  # one status vocabulary with the report skeleton

LABELS = {'news': '뉴스', 'dart': '공시', 'futures': '해외 선물', 'night_futures': '코스피 야간선물',
          'nxt_quotes': 'NXT 장전 호가', 'krx': 'KRX 일별 통계', 'nxt_gap': 'KRX/NXT 괴리율', 'listings': '상장 예정', 'overnight_news': '장 마감 뒤 재료'}
ENGINE_FIELDS = {'market': ('해외 일봉', 'global_daily'), 'news': ('뉴스', 'news'), 'dart': ('공시', 'disclosures'),
                 'overnight_news': ('장 마감 뒤 재료(관심·선정 종목, 10:30까지)', 'overnight_material')}


def engine_data_status(brief, context):
    """Describe the exact filtered snapshot, not whether a model actually received it."""
    sections = brief.get('sections') if isinstance(brief.get('sections'), dict) else {}
    same_snapshot = (isinstance(context, dict) and bool(brief.get('time'))
                     and context.get('as_of') == brief.get('time'))
    sources = context.get('sources', {}) if same_snapshot else {}
    if not isinstance(sources, dict):
        sources = {}
    rows = []
    for key, (label, field) in ENGINE_FIELDS.items():
        section = sections.get(key) if isinstance(sections.get(key), dict) else {}
        if not same_snapshot:
            included = '전달 가능 여부 확인 불가'
        elif context.get(field):
            included = '전달 가능'
        elif key not in sources:
            included = '미포함 · 자료 또는 수집 시각 부적합'
        else:
            included = '미포함 · 출처·상태 또는 유효 자료 없음'
        rows.append((label, section, included))
    for key in ('futures', 'night_futures', 'nxt_quotes', 'krx', 'nxt_gap', 'listings'):
        section = sections.get(key) if isinstance(sections.get(key), dict) else {}
        rows.append((LABELS[key], section, '엔진 브리프 직접 미포함'))
    return rows


def recorded_engine_inputs(root, brief):
    """Latest recorded model inputs for the brief's day; absence is not proof of non-delivery."""
    from sqlite_ro import ro_connect

    result = {}
    try:
        day = date.fromisoformat(brief['trading_day'])
        start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()
        with ro_connect(Path(root) / 'market-research.sqlite3', timeout=0.2) as db:
            for engine, field in (('jev', 'state'),):
                row = db.execute('SELECT time, digest, inputs FROM model_inputs '
                                 'WHERE time >= ? AND time < ? AND engine = ? ORDER BY time DESC LIMIT 1',
                                 (start, end, engine)).fetchone()
                if not row:
                    continue
                payload = json.loads(row[2])
                state = payload.get(field) if isinstance(payload, dict) else None
                context = state.get('premarket_context') if isinstance(state, dict) else None
                result[engine] = {'time': row[0], 'digest': row[1],
                                  'as_of': context.get('as_of') if isinstance(context, dict) else None}
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error, OverflowError):
        pass
    return result


def render_engine_data(brief, root):
    import premarket

    try:
        context = premarket.engine_brief(root=root)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        context = None
    with st.expander('장전 자료 수집·엔진 전달 범위', expanded=False):
        st.caption('아래 전달 가능은 현재 브리프 필터 기준입니다. 실제 모델 입력·수신이나 판단에 반영됐다는 증거는 아닙니다.')
        st.caption('수집 시각 유효성은 브리프 생성 시각 대비 30분 이내를 확인합니다. 브리프는 당일 과거 참고 자료입니다.')
        for label, section, included in engine_data_status(brief, context):
            at = timestamp(section.get('observed_at'))
            collected = f'{at:%m/%d %H:%M} KST' if at else '시각 미확인'
            status = STATUS.get(section.get('status'), '자료 없음' if not section else '상태 미확인')
            source = _text(section.get('source')) or '출처 미확인'
            st.write(f'**{label}** · {status} · {source} · {collected} · {included}')
        recorded = recorded_engine_inputs(root or premarket.ROOT, brief)
        for engine, label in (('jev', 'Jev'),):
            item = recorded.get(engine)
            if not item:
                st.caption(f'{label} 최근 모델 입력 기록 미확인')
                continue
            at, as_of = timestamp(item['time']), timestamp(item['as_of'])
            when = f'{at:%m/%d %H:%M:%S} KST' if at else '기록 시각 미확인'
            reference = f'{as_of:%m/%d %H:%M} KST' if as_of else '장전 맥락 미확인'
            digest = _text(item['digest'])[:12] or '미확인'
            match = ('현재 브리프' if item['as_of'] == brief.get('time') else '다른 브리프') if as_of else '비교 불가'
            st.caption(f'{label} 최근 모델 입력 기록 · {when} · 장전 기준 {reference} ({match}) · 기록 ID {digest}')
        st.caption('상세 분석문은 엔진 브리프에 직접 포함되지 않습니다. 공시는 전일 이전 접수분만, 해외 일봉과 뉴스는 별도 날짜·발행 시각 조건을 통과한 자료만 포함합니다.')


def detailed_report(brief, root):
    day = brief.get('trading_day')
    try:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            return None
        text = (Path(root) / 'reviews' / f'premarket-{day}.md').read_text(encoding='utf-8')
        return text if text.strip() else None
    except (OSError, UnicodeError, ValueError):
        return None


def _text(value):
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    return ''


def _link(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlparse(value)
        return value if url.scheme in ('https', 'http') and url.netloc else None
    except ValueError:
        return None


def ranked_news(section, market=None, day=None):
    """premarket.rank_news: the same scores the premarket report skeleton and the skill use."""
    import premarket
    return premarket.rank_news(section, market, day)


def _market(brief):
    market = ((brief.get('sections') or {}).get('market') or {})
    return market.get('data') if market.get('status') in ('ok', 'partial') and isinstance(market.get('data'), dict) else {}


def highlight_news(section, market=None, day=None):
    from trading_journal import _md, star_text

    st.markdown('#### 오늘 주목할 뉴스')
    news = ranked_news(section, market, day)
    if not news:
        st.caption('순위를 표시할 장전 뉴스가 없습니다.')
        return
    st.caption('매매일지와 같은 분야·핵심어·밤사이 지표·유사 보도 규칙(premarket.rank_news)으로 계산한 참고 순위입니다. 장전 분석의 별점과 같습니다.')
    for item in news[:3]:
        with st.container(border=True):
            stars = item['stars']
            if stars >= 4:
                st.badge(f"{star_text(stars)} · 중요도 {stars}/5 · {item['category']}",
                         color='red' if stars == 5 else 'orange')
                st.markdown(f"**{_md(_text(item['title']))}**")
            else:
                st.markdown(f"**{star_text(stars)} · 중요도 {stars}/5 · {item['category']}**")
                st.write(_text(item['title']))
            at = timestamp(item.get('published'))
            published = at.strftime('%m/%d %H:%M KST') if at else '발행 시각 미확인'
            st.caption(f"{_text(item.get('publisher')) or '매체 미확인'} · {published} · {' · '.join(item['why'])}")
            link = _link(item.get('link'))
            if link:
                st.link_button('기사 보기', link, icon=':material/open_in_new:')




def impact_cards(brief):
    """Build traceable observations and conditional checks from the saved brief."""
    sections = brief.get('sections') or {}
    market_data = _market(brief)
    global_data = market_data.get('global') or {}
    news = ranked_news(sections.get('news'), market_data, brief.get('trading_day'))
    results = []
    indices = [(name, global_data.get(name)) for name in ('나스닥100', 'S&P500', '필라델피아 반도체')]
    for name, item in indices:
        if not isinstance(item, dict) or number(item.get('change_pct'), '+.2f') == '—' or not item.get('date'):
            continue
        scope = '반도체 섹터' if name == '필라델피아 반도체' else '국내 시장'
        direction = '하락' if item['change_pct'] < 0 else '상승' if item['change_pct'] > 0 else '보합'
        effect = ('국내 반도체 종목의 투자심리에' if scope == '반도체 섹터' else '국내 증시의 개장 투자심리에')
        results.append({'scope': scope, 'evidence': f"{name} {item['date']} 전일 대비 {number(item['change_pct'], '+.2f')}%",
                        'check': f"{effect} {direction} 흐름이 전달될 수 있습니다. 개장 후 관련 종목의 방향과 거래량을 확인합니다.",
                        'source': 'Yahoo Finance 일봉', 'link': None})
    sectors = {'반도체': (('반도체', '삼성전자', 'SK하이닉스'), '반도체 업종의 동반 움직임에 영향을 줄 수 있습니다.'),
               '금리·금융': (('금리', '연준', '국채'), '금리 기대 변화가 금융주와 성장주에 서로 다르게 반영될 수 있습니다.'),
               '환율·수출': (('환율', '달러'), '환율 변화가 수출주와 외국인 수급에 영향을 줄 수 있습니다.'),
               '원자재·에너지': (('유가', '원유', '에너지'), '원가 부담과 에너지 업종의 기대가 달라질 수 있습니다.')}
    for label, (words, effect) in sectors.items():
        item = next((row for row in news if any(word.casefold() in row['title'].casefold() for word in words)), None)
        if item:
            results.append({'scope': label, 'evidence': item['title'],
                            'check': effect + ' 관련 섹터의 시초가와 수급을 확인합니다. 기사 제목만으로 방향을 확정할 수 없습니다.',
                            'source': item.get('publisher') or '매체 미확인', 'link': item.get('link'),
                            'published': item.get('published')})
    krx = sections.get('krx') or {}
    companies = krx.get('data') if krx.get('status') in ('ok', 'partial') else []
    for company in companies or []:
        if not isinstance(company, dict):
            continue
        name = _text(company.get('ISU_NM')).strip()
        if len(name) < 2:
            continue
        item = next((row for row in news if name.casefold() in row['title'].casefold()), None)
        if item:
            results.append({'scope': name, 'evidence': item['title'],
                            'check': '기업 관련 내용을 원문과 공시에서 확인하고 개장 후 호가·거래량을 대조합니다.',
                            'source': item.get('publisher') or '매체 미확인', 'link': item.get('link'),
                            'published': item.get('published')})
    return results


def render_impacts(brief):
    st.markdown('#### 시장·섹터·종목 영향 점검')
    st.caption('연관 자료와 개장 후 확인할 항목입니다. 가격 영향은 시초가에 이미 반영됐을 수 있습니다.')
    cards = impact_cards(brief)
    if not cards:
        st.info('연결할 근거 자료가 부족합니다. 장전 자료의 수집 상태를 확인하세요.')
        return
    for item in cards:
        with st.container(border=True):
            st.markdown(f"**{item['scope']}**")
            st.write(item['evidence'])
            st.caption(item['check'])
            at = timestamp(item.get('published'))
            published = f" · {at:%m/%d %H:%M} KST" if at else ''
            st.caption(f"근거 · {item['source']}{published}")
            link = _link(item.get('link'))
            if link:
                st.link_button('근거 기사 보기', link, icon=':material/open_in_new:')


def render(root=None):
    import premarket

    st.subheader('오늘의 장전 분석')
    brief = premarket.load(root=root)
    if not isinstance(brief, dict):
        st.info('오늘 수집된 장전 분석이 없습니다. 이전 날짜의 자료는 오늘 자료로 표시하지 않습니다.')
        return
    observed = timestamp(brief.get('time'))
    st.caption(f"장전 기록 · {observed:%Y-%m-%d %H:%M} KST" if observed else '장전 기록 · 수집 시각 미확인')
    render_engine_data(brief, root)
    report = detailed_report(brief, root or premarket.ROOT)
    if report:
        from premarket_template import check
        problems = check(report, written=True, day=brief.get('trading_day'))
        if problems:
            st.warning('장전 상세 분석이 공통 템플릿과 다릅니다: ' + ' · '.join(problems), icon=':material/rule:')
        with st.expander('오늘의 장전 상세 분석', expanded=True):
            st.markdown(for_streamlit(report))
    else:
        st.caption('오늘의 장전 상세 분석은 아직 작성되지 않았습니다.')
    summary = [item for item in brief.get('summary') or [] if _text(item)]
    if summary:
        with st.container(border=True):
            st.markdown('#### 장전 시황 요약')
            for item in summary:
                st.write('• ' + _text(item))

    strategy = [item for item in brief.get('strategy') or [] if _text(item)]
    render_impacts(brief)

    st.markdown('#### 오늘의 트레이딩 전략')
    if strategy:
        with st.container(border=True):
            for item in strategy:
                st.write('• ' + _text(item))
    else:
        st.info('오늘의 전략 자료가 없습니다.')

    sections = brief.get('sections') if isinstance(brief.get('sections'), dict) else {}
    highlight_news(sections.get('news'), _market(brief), brief.get('trading_day'))
    st.caption('국내 일봉·수급과 해외 일봉 전체는 위의 「국내 지수·수급」과 「해외 지표」에 있습니다.')

    missing = []
    for key, label in LABELS.items():
        section = sections.get(key)
        if not isinstance(section, dict):
            missing.append(f'{label}: 자료 없음')
        elif section.get('status') != 'ok':
            status = STATUS.get(section.get('status'), '상태 미확인')
            missing.append(f'{label}: {status}')
    if missing:
        st.caption('자료 상태 · ' + ' · '.join(missing))
    key = st.selectbox('장전 근거 자료', list(LABELS), format_func=LABELS.__getitem__, key='premarket_detail')
    section = sections.get(key)
    if not isinstance(section, dict):
        st.info('선택한 자료가 없습니다.')
        return
    with st.container(border=True):
        collected = timestamp(section.get('observed_at'))
        collected_label = collected.strftime('%m/%d %H:%M KST') if collected else '미확인'
        meta = [f"출처: {_text(section.get('source')) or '미확인'}",
                f'수집: {collected_label}']
        if section.get('note'):
            meta.append(_text(section['note']))
        if section.get('error'):
            meta.append(f"사유: {_text(section['error']) or '확인 필요'}")
        st.caption(' · '.join(meta))
        if key == 'krx':
            st.caption('출처: 한국거래소 통계정보 · 일별 통계의 기준일을 확인하세요.')
        elif key == 'dart':
            st.caption('공시 접수 시간은 확인되지 않을 수 있습니다. 접수일만으로 장전 공시라고 판단하지 마세요.')
        data = section.get('data')
        if key in ('news', 'dart') and isinstance(data, list):
            rows = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                if key == 'news':
                    at = timestamp(item.get('published'))
                    day = at.strftime('%m/%d %H:%M') if at else '미확인'
                    by = _text(item.get('publisher'))
                else:
                    day = _text(item.get('receipt_date'))
                    day = f'{day[:4]}-{day[4:6]}-{day[6:]}' if len(day) == 8 and day.isdigit() else '미확인'
                    by = _text(item.get('company'))
                rows.append({'제목': _text(item.get('title')) or '제목 미확인', '매체' if key == 'news' else '기업': by,
                             '발행 (KST)' if key == 'news' else '접수일': day, '출처 링크': _link(item.get('link'))})
            if rows:
                st.dataframe(rows, hide_index=True, alt=LABELS[key],
                             column_config={'출처 링크': st.column_config.LinkColumn(display_text='기사 열기' if key == 'news' else '공시 열기')})
            else:
                st.caption('표시할 자료가 없습니다.')
        elif key == 'futures' and isinstance(data, dict) and data:
            rows = [{'상품': name, '가격': value.get('price'), '비교값': value.get('previous'),
                     '등락률 %': value.get('pct'), '시세 기준 시각': '미확인'}
                    for name, value in data.items() if isinstance(value, dict)]
            if rows:
                st.dataframe(rows, hide_index=True, alt='해외 선물 자료')
            else:
                st.caption('표시할 선물 자료가 없습니다.')
        elif key == 'nxt_quotes' and isinstance(data, list) and data:
            rows = [{'종목 코드': x.get('code'), '가격': x.get('price'), '매수호가': x.get('bid'),
                     '매도호가': x.get('ask'), '호가 시각': x.get('quote_time')}
                    for x in data if isinstance(x, dict)]
            st.dataframe(rows, hide_index=True, alt='NXT 장전 호가')
        elif key == 'overnight_news' and isinstance(data, dict) and data:
            counts, nxt = data.get('counts') or {}, data.get('nxt') or {}
            st.caption(f"{data.get('scanned', 0)}종목 확인 · 호재 {counts.get('호재', 0)} · 악재 {counts.get('악재', 0)} · 혼재 {counts.get('혼재', 0)} · "
                       '제목을 규칙으로 나눈 참고 분류이며 매수 신호가 아닙니다.')
            rows = [{'구분': r.get('direction'), '종목': r.get('name'), '코드': r.get('code'), '재료': '·'.join(dict.fromkeys(i['type'] for i in r['items'])),
                     '프리마켓 등락 % (키움)': (nxt.get(r.get('code')) or {}).get('change_pct'), '제목': r['items'][0]['title'],
                     '시각': r['items'][0]['time'][5:16].replace('T', ' '), '출처 링크': _link(r['items'][0].get('link'))}
                    for r in data.get('rows') or [] if r.get('items')]
            if rows:
                st.dataframe(rows, hide_index=True, alt='장 마감 뒤 호재·악재 종목', column_config={'출처 링크': st.column_config.LinkColumn(display_text='기사 열기')})
            if data.get('overseas'):
                from premarket import OVERSEAS_TONE
                st.markdown('##### 밤사이 해외 업종')
                st.caption('기사 방향은 해외 업종 기사 제목(로이터)의 오름·내림이며 국내 종목의 호재·악재가 아닙니다.')
                st.dataframe([{'해외 업종': s['signal'], '등락 %': s['move_pct'], '평소보다 큼': '예' if s['big'] else '', '기사 방향': OVERSEAS_TONE.get(s['news'], '—'),
                               '관련 국내 종목': '·'.join(s['names']), '기사': s['items'][0]['title'] if s['items'] else '',
                               '출처 링크': _link(s['items'][0].get('link')) if s['items'] else None} for s in data['overseas']],
                             hide_index=True, alt='밤사이 해외 업종', column_config={'출처 링크': st.column_config.LinkColumn(display_text='기사 열기')})
        elif key == 'krx' and isinstance(data, list) and data:
            rows = [{'기준일': x.get('BAS_DD'), '종목': x.get('ISU_NM'), '코드': x.get('ISU_CD'),
                     '종가': x.get('TDD_CLSPRC'), '등락률 %': x.get('FLUC_RT'), '거래량': x.get('ACC_TRDVOL')}
                    for x in data if isinstance(x, dict)]
            st.dataframe(rows, hide_index=True, alt='한국거래소 일별 통계')
        elif key in ('listings', 'nxt_gap'):  # Kiwoom / Toss sections (2026-11-17), not sent to the engines
            import broker_data_view
            (broker_data_view.premarket_listings if key == 'listings' else broker_data_view.premarket_nxt_gap)(data)
        elif key == 'night_futures' and isinstance(data, dict) and data.get('traded_at'):
            at = timestamp(data['traded_at'])
            st.write(f"마지막 관측 {at:%m/%d %H:%M} KST" if at else '관측 시각 미확인')
            st.write(f"관측 가격 {number(data.get('price'), ',.2f')} · 등락률 {number(data.get('pct'), '+.2f')}%")
        else:
            st.caption('표시할 자료가 없습니다.')
