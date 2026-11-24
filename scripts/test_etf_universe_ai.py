import json
import math
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from etf_universe import NAMES, SYMBOLS
import nh_etf_auto_v2 as ai


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, *args):
        return json.dumps(self.data).encode()


class EtfUniverseAITest(unittest.TestCase):
    def setUp(self):
        self.bars = {name: [{'stck_prpr': str(100 + i)} for i in range(6)] for name in SYMBOLS}
        self.news = [{'title': '시장 상황', 'published': datetime.now(ai.old.KST).isoformat()}]

    def test_universe_contains_only_requested_products(self):
        self.assertEqual(SYMBOLS, {'K200': '069500', 'LEV': '122630', 'INV': '114800',
                                   'KQ150': '229200', 'KQ150LEV': '233740',
                                   'KQ150INV': '251340', 'SEMILEV': '494310'})
        self.assertEqual(set(NAMES), set(SYMBOLS))

    def test_decision_questions_and_each_allowed_result(self):
        seen = []
        def respond(req, timeout):
            payload = json.loads(req.data)
            self.assertEqual(set(payload['input'][1]['content'][0]['data']['bars_newest_first']), set(SYMBOLS))
            self.assertEqual({x['value'] for x in payload['questions'][0]['choices']}, {'WAIT', *SYMBOLS})
            return Response({'answers': [{'type': 'choice', 'choice': seen[-1], 'confidence': .7}], 'usage': {}})
        with patch.object(ai.old, 'dotenv_values', return_value={'OPENAI_API_KEY': 'fake'}), \
                patch.object(ai.urllib.request, 'urlopen', side_effect=respond):
            for choice in ['WAIT', *SYMBOLS]:
                seen.append(choice)
                result = ai.decision7(self.bars, self.news)
                self.assertEqual(result[:2], (choice, .7))
                self.assertIsNotNone(datetime.fromisoformat(result[2]['response_time']).utcoffset())

    def test_holding_questions_restrict_symbols_and_responses(self):
        def respond(req, timeout):
            payload = json.loads(req.data)
            self.assertEqual({x['value'] for x in payload['questions'][0]['choices']}, {'HOLD', 'SELL'})
            self.assertEqual(payload['input'][1]['content'][0]['data']['symbol'], '494310')
            return Response({'answers': [{'type': 'choice', 'choice': 'SELL', 'confidence': .8}]})
        with patch.object(ai.old, 'dotenv_values', return_value={'OPENAI_API_KEY': 'fake'}), \
                patch.object(ai.urllib.request, 'urlopen', side_effect=respond) as network:
            self.assertEqual(ai.holding_decision7(self.bars, self.news, '494310', 100, 101)[:2], ('SELL', .8))
            for symbol, avg in [(None, 100), ('005930', 100), ('494310', math.nan)]:
                with self.assertRaises(ValueError):
                    ai.holding_decision7(self.bars, self.news, symbol, avg, 101)
            self.assertEqual(network.call_count, 1)

    def test_jev_sends_all_candidates_and_validates_response(self):
        def respond(req, timeout):
            payload = json.loads(req.data)
            self.assertEqual(set(payload['questions']['candidate']['criteria']), {'WAIT', *SYMBOLS})
            self.assertTrue(next(iter(payload['assets'].values()))['data_base64'])
            return Response({'model': 'jev-1.14.0', 'answers': {'candidate':
                {'type': 'choice', 'choice': 'KQ150INV', 'confidence': .75}}})
        with patch.object(ai.old, 'dotenv_values', return_value={'JEV_API_KEY': 'fake'}), \
                patch.object(ai.urllib.request, 'urlopen', side_effect=respond):
            result = ai.jev7(self.bars, self.news)
            self.assertEqual(result['candidate']['choice'], 'KQ150INV')
            self.assertEqual(len(result['chart_sha256']), 64)
            self.assertIsNotNone(datetime.fromisoformat(result['response_time']).utcoffset())

    def test_jev_charts_scanner_stocks_and_offers_them_as_choices(self):
        sent = []
        def respond(req, timeout):
            sent.append(json.loads(req.data))
            return Response({'model': 'jev-1.14.0', 'answers': {'candidate': {'type': 'choice', 'choice': '005930', 'confidence': .8}}})
        bars = {**self.bars, '005930': self.bars['K200']}
        with patch.object(ai.old, 'dotenv_values', return_value={'JEV_API_KEY': 'fake'}), \
                patch.object(ai.urllib.request, 'urlopen', side_effect=respond):
            result = ai.jev7(bars, self.news, candidates={'005930': '삼성전자', '000660': 'no bars, dropped'})
        self.assertEqual(result['candidate']['choice'], '005930')
        criteria = sent[0]['questions']['candidate']['criteria']
        self.assertIn('005930', criteria)
        self.assertNotIn('000660', criteria)
        for bad in ({**self.bars, 'SAMSUNG': self.bars['K200']}, {k: v for k, v in self.bars.items() if k != 'INV'}):
            with self.assertRaises(ValueError):
                ai._etf7_inputs(bad, self.news)

    def test_jev_rechecks_market_headlines_at_dispatch_and_records_the_actual_input(self):
        at = datetime(2026, 11, 23, 12, 31, 24, 80000, tzinfo=ai.old.KST)
        expired = {'title': '경계가 지난 기사', 'published': (at - timedelta(minutes=90, milliseconds=80)).isoformat()}
        fresh = {'title': '최근 기사', 'published': (at - timedelta(minutes=10)).isoformat()}

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return at

        sent = []
        def respond(req, timeout):
            sent.append(json.loads(req.data)['state'])
            return Response({'model': 'jev-1.14.0', 'answers': {'candidate': {
                'type': 'choice', 'choice': 'WAIT', 'confidence': 0.7}}})

        with patch.object(ai, 'datetime', Clock), patch.object(ai.old, 'dotenv_values', return_value={'JEV_API_KEY': 'fake'}), \
                patch.object(ai.urllib.request, 'urlopen', side_effect=respond) as network, \
                patch('research_store.record_input', return_value='a' * 64) as log:
            ai.jev7(self.bars, [expired, fresh])
            with patch('etf_news.no_fresh_news', return_value={'news_status': 'none_recent'}):
                for news in ([expired], []):  # user 2026-11-24: no fresh story does not stop the judgment, Jev is told so
                    ai.jev7(self.bars, news)
        self.assertEqual(network.call_count, 3)
        self.assertNotIn('news_status', sent[0])
        self.assertTrue(all(s['recent_news_headlines'] == [] and s['news_status'] == 'none_recent' for s in sent[1:]))
        with self.assertRaises(ValueError):
            ai.decision7(self.bars, [])  # the OpenAI judge still needs news
        self.assertEqual(sent[0]['recent_news_headlines'], [{**fresh, 'news_role': 'priority'}])
        self.assertEqual((sent[0]['news_priority_minutes'], sent[0]['news_max_age_minutes']), (30, 90))
        self.assertEqual(sent[0]['news_checked_at'], at.isoformat())
        self.assertEqual(log.call_args_list[0].args[2]['state'], sent[0])

    def test_jev_receives_investor_flows_when_available(self):
        sent = []
        def respond(req, timeout):
            sent.append(json.loads(req.data)['state'])
            return Response({'model': 'jev-1.14.0', 'answers': {'candidate': {'type': 'choice', 'choice': 'WAIT', 'confidence': .5}}})
        flows = {'K200': {'last_close_day': {'date': '20261110', 'foreign': 1, 'institution': 2, 'individual': -3}}}
        with patch.object(ai.old, 'dotenv_values', return_value={'JEV_API_KEY': 'fake'}), \
                patch.object(ai.urllib.request, 'urlopen', side_effect=respond):
            ai.jev7(self.bars, self.news, investor=flows)
            ai.jev7(self.bars, self.news)
        self.assertEqual(sent[0]['investor_daily_net_qty'], flows)
        self.assertNotIn('investor_daily_net_qty', sent[1])

    def test_jev_holding_is_distinct_and_sends_only_market_and_position(self):
        def respond(req, timeout):
            payload = json.loads(req.data)
            self.assertEqual({q for q in payload['questions'] if not q.startswith('why_')}, {'holding'})  # + evidence questions
            self.assertEqual(set(payload['questions']['holding']['criteria']), {'HOLD', 'SELL'})
            self.assertEqual(payload['state']['position'], {'symbol': '114800', 'entry_average': 100, 'bid': 99})
            self.assertNotIn('account', json.dumps(payload))
            self.assertTrue(next(iter(payload['assets'].values()))['data_base64'])
            return Response({'model': 'jev-1.14.0', 'answers': {'holding':
                {'type': 'choice', 'choice': 'SELL', 'confidence': .8, 'probabilities': {'HOLD': .2, 'SELL': .8}}}})
        with patch.object(ai.old, 'dotenv_values', return_value={'JEV_API_KEY': 'fake'}), \
                patch.object(ai.urllib.request, 'urlopen', side_effect=respond):
            result = ai.jev_holding7(self.bars, self.news, '114800', 100, 99)
            self.assertEqual(result['holding']['choice'], 'SELL')
            self.assertEqual(result['holding']['confidence'], .8)
            self.assertNotIn('candidate', result)
            self.assertIsNotNone(datetime.fromisoformat(result['response_time']).utcoffset())

    def test_jev_holding_rejects_invalid_inputs_and_ambiguous_responses(self):
        with patch.object(ai.old, 'dotenv_values', return_value={'JEV_API_KEY': 'fake'}), \
                patch.object(ai.urllib.request, 'urlopen') as network:
            for symbol, avg, bid in [(None, 100, 100), ('005930', 100, 100),
                                     ('114800', math.nan, 100), ('114800', 100, 0)]:
                with self.assertRaises(ValueError):
                    ai.jev_holding7(self.bars, self.news, symbol, avg, bid)
            with self.assertRaises(ValueError):
                ai.jev_holding7(self.bars, [{'title': '시장', 'published': 'not-a-date'}], '114800', 100, 100)
            network.assert_not_called()
            for result in [
                {'model': 'jev-1.14.0', 'answers': {'candidate': {'type': 'choice', 'choice': 'SELL', 'confidence': .8}}},
                {'model': 'jev-1.14.0', 'answers': {'holding': {'type': 'choice', 'choice': 'WAIT', 'confidence': .8}}},
                {'model': 'jev-1.14.0', 'answers': {'holding': {'type': 'choice', 'choice': 'SELL', 'confidence': True}}},
                {'model': 'jev-1.14.0', 'answers': {'holding': {'type': 'choice', 'choice': 'SELL', 'confidence': .8,
                                                                'probabilities': {'SELL': float('nan'), 'HOLD': .2}}}},
            ]:
                network.return_value = Response(result)
                with self.assertRaises(ValueError):
                    ai.jev_holding7(self.bars, self.news, '114800', 100, 100)
            network.side_effect = TimeoutError('connection failure')
            with self.assertRaises(TimeoutError):
                ai.jev_holding7(self.bars, self.news, '114800', 100, 100)

    def test_invalid_data_and_model_answers_fail_closed(self):
        credentials = {'OPENAI_API_KEY': 'fake', 'JEV_API_KEY': 'fake'}
        with patch.object(ai.old, 'dotenv_values', return_value=credentials), \
                patch.object(ai.urllib.request, 'urlopen') as network:
            for bars, news in [({}, self.news), (self.bars, []),
                               ({**self.bars, 'K200': [{'stck_prpr': 'NaN'}] * 6}, self.news)]:
                with self.assertRaises(ValueError):
                    ai.decision7(bars, news)
            network.assert_not_called()
            for result in [[], {'answers': [{'type': 'choice', 'choice': '005930', 'confidence': .8}]},
                           {'answers': [{'type': 'choice', 'choice': 'INV', 'confidence': True}]}]:
                network.return_value = Response(result)
                with self.assertRaises(ValueError):
                    ai.decision7(self.bars, self.news)
            for result in [[], {'model': 'wrong', 'answers': {'candidate': {'type': 'choice', 'choice': 'INV', 'confidence': .8}}},
                           {'model': 'jev-1.14.0', 'answers': {'candidate': {'type': 'choice', 'choice': 'INV', 'confidence': float('nan')}}}]:
                network.return_value = Response(result)
                with self.assertRaises(ValueError):
                    ai.jev7(self.bars, self.news)


if __name__ == '__main__':
    unittest.main()
