import json
import unittest
from unittest.mock import patch, Mock
from src.tools.krx_daily_tool import KrxDailyTool

class KrxDailyTests(unittest.TestCase):
    @patch.dict('os.environ', {'KRX_API_KEY':'test-key'})
    @patch('src.tools.krx_daily_tool.requests.get')
    def test_filters_and_does_not_forward_key_on_redirect(self, get):
        response=Mock(status_code=200);response.json.return_value={'OutBlock_1':[{'BAS_DD':'2026/11/05','ISU_CD':'005930','TDD_CLSPRC':'100,000'},{'ISU_CD':'000660'}]};get.return_value=response
        data=json.loads(KrxDailyTool().execute(date='2026-11-05',symbol='005930.KS'))
        self.assertTrue(data['ok']);self.assertEqual(len(data['records']),1)
        self.assertEqual(get.call_args.kwargs['allow_redirects'],False)
        self.assertEqual(get.call_args.kwargs['headers']['AUTH_KEY'],'test-key')
    @patch.dict('os.environ', {'KRX_API_KEY':'test-key'})
    @patch('src.tools.krx_daily_tool.requests.get')
    def test_denied_is_not_empty_success(self,get):
        get.return_value=Mock(status_code=401)
        result=json.loads(KrxDailyTool().execute(date='2026-11-05',symbol='005930.KS'))
        self.assertFalse(result['ok']);self.assertIn('401',result['error'])
    @patch('src.tools.krx_daily_tool.requests.get')
    def test_future_date_no_request(self,get):
        result=json.loads(KrxDailyTool().execute(date='2999-01-01',symbol='005930.KS'))
        self.assertFalse(result['ok']);get.assert_not_called()
