import json
import unittest
from unittest.mock import patch
import nh_jev_compare as j

class JevComparisonTests(unittest.TestCase):
    def test_openai_cost_distinguishes_cache(self):
        usage={'input_tokens':2000,'input_tokens_details':{'cached_tokens':1000,'cache_write_tokens':500,
               'cache_write_tokens_details':{'ttl_30m_tokens':500,'ttl_12h_tokens':0}}}
        self.assertAlmostEqual(j.openai_cost(usage),0.0001225)

    def test_chart_and_request_use_image_header(self):
        row = {'bsop_time':'110000','stck_prpr':'100'}
        bars = {name:[row,row] for name in ('K200','INV','LEV','SAMSUNG','NAVER','KAKAO')}
        class Result:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, *args):
                return json.dumps({'model':'jev-1.14.0','answers':{'candidate':{'choice':'WAIT'},'direction':{'choice':'up'}},'usage':{'input_tokens':1000}}).encode()
        def call(req, timeout):
            self.assertEqual(req.get_header('Typesafe-danger-zone-images'), 'true')
            data=json.loads(req.data)
            self.assertEqual(data['model'],'jev-1.14.0')
            self.assertIn(data['state']['chart'], data['assets'])
            self.assertTrue(data['assets'][data['state']['chart']]['data_base64'])
            self.assertEqual(set(data['questions']['candidate']['criteria']), set(bars) | {'WAIT'})
            return Result()
        with patch.object(j,'dotenv_values',return_value={'JEV_API_KEY':'test'}), patch.object(j.urllib.request,'urlopen',side_effect=call):
            answer=j.jev(bars,[])
        self.assertEqual(answer['candidate']['choice'],'WAIT')
        self.assertAlmostEqual(answer['estimated_usd'],0.000042)

    def test_missing_key_fails_closed(self):
        with patch.object(j,'dotenv_values',return_value={}):
            with self.assertRaises(ValueError): j.jev({},[])

if __name__=='__main__': unittest.main()
