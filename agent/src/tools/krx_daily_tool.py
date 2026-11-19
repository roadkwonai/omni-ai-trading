"""Read one completed KRX market-day snapshot from the official Open API."""
from __future__ import annotations

import json
import re
from datetime import date as calendar_date, datetime
from zoneinfo import ZoneInfo

import requests

from src.agent.tools import BaseTool
from src.config.accessor import get_env_config


class KrxDailyTool(BaseTool):
    name = 'krx_daily_snapshot'
    description = ('공식 KRX Open API의 특정 완료 거래일 유가증권/코스닥 일별 매매 원자료. '
                   '권한 승인 필요. 수정주가가 아닌 원자료이므로 액면분할 등의 백테스트에 주의.')
    parameters = {
        'type': 'object',
        'properties': {
            'date': {'type': 'string', 'description': '완료 거래일 YYYY-MM-DD (한국시간 오늘 제외)'},
            'symbol': {'type': 'string', 'description': '6자리 종목코드.KS 또는 .KQ'},
        },
        'required': ['date', 'symbol'],
    }
    is_readonly = True

    def execute(self, date: str = '', symbol: str = '', **kwargs) -> str:
        def result(value):
            return json.dumps(value, ensure_ascii=False)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(date)):
            return result({'ok': False, 'error': 'date must be YYYY-MM-DD'})
        try:
            day = calendar_date.fromisoformat(date)
        except ValueError:
            return result({'ok': False, 'error': 'invalid date'})
        if day >= datetime.now(ZoneInfo('Asia/Seoul')).date():
            return result({'ok': False, 'error': 'only completed prior days are available'})
        match = re.fullmatch(r'(\d{6})\.(KS|KQ)', str(symbol).strip().upper())
        if not match:
            return result({'ok': False, 'error': 'symbol must be six digits followed by .KS or .KQ'})
        key = get_env_config().data.krx_api_key.strip()
        if not key:
            return result({'ok': False, 'error': 'KRX_API_KEY is not configured'})
        endpoint = 'stk_bydd_trd' if match.group(2) == 'KS' else 'ksq_bydd_trd'
        try:
            response = requests.get('https://data-dbg.krx.co.kr/svc/apis/sto/' + endpoint,
                                    params={'basDd': day.strftime('%Y%m%d')},
                                    headers={'AUTH_KEY': key}, timeout=20, allow_redirects=False)
            if response.status_code != 200:
                return result({'ok': False, 'error': f'KRX HTTP {response.status_code}; check API service approval'})
            payload = response.json()
        except (requests.RequestException, ValueError):
            return result({'ok': False, 'error': 'KRX request failed or returned invalid JSON'})
        rows = payload.get('OutBlock_1') if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return result({'ok': False, 'error': 'KRX response missing OutBlock_1'})
        records = [row for row in rows if isinstance(row, dict) and str(row.get('ISU_CD')) == match.group(1)]
        return result({'ok': True, 'source': 'KRX Open API', 'date': date,
                       'symbol': match.group(0), 'adjustment': 'raw', 'currency': 'KRW',
                       'records': records, 'note': '빈 결과는 휴장 또는 해당 일자 자료 부재일 수 있습니다.'})
