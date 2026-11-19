import json
from unittest import TestCase
from unittest.mock import patch, Mock
from src.tools.opendart_tool import OpenDartTool

class TestOpenDart(TestCase):
    @patch.dict('os.environ', {'OPENDART_API_KEY': 'private-key'})
    @patch('src.tools.opendart_tool.requests.get')
    def test_disclosures_and_secret_safe_failure(self, get):
        get.return_value = Mock(status_code=200, json=lambda: {'status':'000','list':[{'rcept_no':'1'}],'total_page':2})
        result=json.loads(OpenDartTool().execute(action='disclosures', corp_code='00126380', start_date='20260101',end_date='20260201'))
        self.assertEqual(result['data']['total_page'],2)
        self.assertEqual(get.call_args.kwargs['params']['corp_code'],'00126380')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        get.side_effect=Exception('private-key')
        self.assertNotIn('private-key',OpenDartTool().execute(action='disclosures',corp_code='00126380',start_date='20260101',end_date='20260201'))

    @patch.dict('os.environ', {'OPENDART_API_KEY': 'key'})
    @patch('src.tools.opendart_tool.requests.get')
    def test_financials_and_validation(self,get):
        get.return_value=Mock(status_code=200,json=lambda:{'status':'013','message':'no data'})
        result=json.loads(OpenDartTool().execute(action='financials',corp_code='00126380',year='2025',report_code='11011'))
        self.assertEqual(result['status'],'no_data')
        self.assertEqual(get.call_args.kwargs['params']['reprt_code'],'11011')
        self.assertEqual(json.loads(OpenDartTool().execute(action='financials',corp_code='bad',year='2025',report_code='11011'))['status'],'error')

    @patch.dict('os.environ', {'OPENDART_API_KEY': 'key'})
    @patch('src.tools.opendart_tool.requests.get')
    def test_company_lookup_and_private_cache(self,get):
        import io, zipfile, tempfile
        from pathlib import Path
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('CORPCODE.xml','<result><list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code></list></result>')
        response=Mock(status_code=200,content=archive.getvalue())
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ',{'VIBE_TRADING_HOME':d}):
            get.return_value=response
            result=json.loads(OpenDartTool().execute(action='company',stock_code='005930'))
            self.assertEqual(result['data']['corp_code'],'00126380')
            self.assertEqual(json.loads(OpenDartTool().execute(action='company',stock_code='005930'))['status'],'ok')
            self.assertEqual(get.call_count,1)
            self.assertEqual((Path(d)/'cache/opendart-corpcode.xml').stat().st_mode & 0o777,0o600)
