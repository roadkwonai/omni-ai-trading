import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from paper_learning import evaluate


def event(at, bar, price=100, symbol='K200'):
    return {'time':f'2026-11-06T{at}+09:00','event':'decision','choice':'WAIT',
            'market_bars':{symbol:[{'bsop_time':bar,'stck_prpr':str(price)}]}}


def ledger(path, records):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, digest TEXT, time TEXT, event TEXT, data TEXT)')
        for i, item in enumerate(records):
            db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?,?)',('s',i,'digest',item['time'],item['event'],json.dumps(item)))


class ObservationTests(unittest.TestCase):
    def test_jev_only_holding_prices_mature_entry_without_becoming_entry_sample(self):
        from etf_universe import SYMBOLS
        with tempfile.TemporaryDirectory() as temp:
            source,out=Path(temp)/'ledger.db',Path(temp)/'observations.db'
            def context(at,bar,price,kind,cycle):
                result = {'time':f'2026-11-06T{at}+09:00','event':'market_context',
                        'universe':'etf7','context':kind,'cycle_id':cycle*32,
                        'market_bars':{name:[{'bsop_time':bar,'stck_prpr':str(price)}] for name in SYMBOLS}}
                if kind == 'holding': result['symbol'] = SYMBOLS['K200']
                return result
            ledger(source,[context('10:00:30','095800',100,'entry','a'),
                           context('10:08:00','100600',105,'holding','b')])
            self.assertEqual(evaluate(source,out)['observed'],7)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT DISTINCT offset FROM price_observations').fetchall(),[(0,)])
                self.assertEqual(db.execute("SELECT universe,status FROM price_observations WHERE symbol='K200'").fetchone(),
                                 ('etf7_jev_only','observed'))

    def test_unknown_holding_symbol_cannot_mature_entry(self):
        from etf_universe import SYMBOLS
        with tempfile.TemporaryDirectory() as temp:
            source,out=Path(temp)/'ledger.db',Path(temp)/'observations.db'
            def context(at,bar,price,kind,cycle):
                result = {'time':f'2026-11-06T{at}+09:00','event':'market_context',
                        'universe':'etf7','context':kind,'cycle_id':cycle*32,
                        'market_bars':{name:[{'bsop_time':bar,'stck_prpr':str(price)}] for name in SYMBOLS}}
                if kind == 'holding': result['symbol'] = '005930'
                return result
            ledger(source,[context('10:00:30','095800',100,'entry','a'),
                           context('10:08:00','100600',105,'holding','b')])
            self.assertEqual(evaluate(source,out)['observed'],0)


    def test_jev_only_context_produces_distinct_observations_without_decision_choice(self):
        from etf_universe import SYMBOLS
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp)/'ledger.db', Path(temp)/'observations.db'
            first = {'time':'2026-11-06T10:00:30+09:00','event':'market_context',
                     'universe':'etf7','context':'entry','cycle_id':'a'*32,
                     'market_bars':{name:[{'bsop_time':'095800','stck_prpr':'100'}] for name in SYMBOLS}}
            second = {'time':'2026-11-06T10:08:00+09:00','event':'market_context',
                      'universe':'etf7','context':'entry','cycle_id':'b'*32,
                      'market_bars':{name:[{'bsop_time':'100600','stck_prpr':'105'}] for name in SYMBOLS}}
            ledger(source, [first, second])
            self.assertEqual(evaluate(source, out)['observed'], 7)
            self.assertEqual(evaluate(source, out)['observed'], 7)
            with sqlite3.connect(out) as db:
                rows = db.execute('SELECT choice,universe,status FROM price_observations WHERE offset=0').fetchall()
            self.assertEqual(len(rows), 7)
            self.assertTrue(all(row == (None,'etf7_jev_only','observed') for row in rows))

    def test_malformed_jev_context_never_creates_observations(self):
        from etf_universe import SYMBOLS
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp)/'ledger.db', Path(temp)/'observations.db'
            base = {'time':'2026-11-06T10:00:30+09:00','event':'market_context',
                    'universe':'etf7','context':'entry','cycle_id':'a'*32,
                    'market_bars':{name:[{'bsop_time':'095800','stck_prpr':'100'}] for name in SYMBOLS}}
            records = [{**base,'cycle_id':'bad'}, {**base,'choice':'K200'},
                       {**base,'market_bars':{'K200':base['market_bars']['K200']}}]
            ledger(source, records)
            evaluate(source, out)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM price_observations').fetchone()[0], 0)

    def test_jev_context_timestamp_must_match_ledger_timestamp(self):
        from etf_universe import SYMBOLS
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp)/'ledger.db', Path(temp)/'observations.db'
            first = {'time':'2026-11-06T10:00:30+09:00','event':'market_context',
                     'universe':'etf7','context':'entry','cycle_id':'a'*32,
                     'market_bars':{name:[{'bsop_time':'095800','stck_prpr':'100'}] for name in SYMBOLS}}
            ledger(source,[first])
            with sqlite3.connect(source) as db:
                db.execute("UPDATE events_v2 SET time='2026-11-06T10:01:30+09:00'")
            evaluate(source,out)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM price_observations').fetchone()[0],0)

    def test_two_symbol_decisions_are_evaluated_without_future_leak(self):
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp)/'ledger.db', Path(temp)/'observations.db'
            first = event('10:00:30','095800',100)
            first['market_bars']['INV'] = [{'bsop_time':'095800','stck_prpr':'200'}]
            later = event('10:08:00','100600',105)
            later['market_bars']['INV'] = [{'bsop_time':'100600','stck_prpr':'190'}]
            ledger(source, [first, later])
            self.assertEqual(evaluate(source, out)['observed'], 2)
            with sqlite3.connect(out) as db:
                rows = db.execute("SELECT symbol, change_pct, status FROM price_observations WHERE offset=0 ORDER BY symbol").fetchall()
            result = {symbol:(change, status) for symbol, change, status in rows}
            self.assertEqual((DecimalValue(result['K200'][0]), result['K200'][1]), (5, 'observed'))
            self.assertEqual((DecimalValue(result['INV'][0]), result['INV'][1]), (-5, 'observed'))
            self.assertNotIn('LEV', result)

    def test_future_bar_is_the_first_in_the_five_to_seven_minute_window(self):
        def future(records):
            with tempfile.TemporaryDirectory() as temp:
                source, out = Path(temp)/'ledger.db', Path(temp)/'observations.db'
                ledger(source, records); evaluate(source, out)
                with sqlite3.connect(out) as db:
                    return db.execute('SELECT future_time,future_close,status FROM price_observations WHERE offset=0').fetchone()
        decision = event('10:00:00', '095800', 100)
        bad_clock = event('10:06:00', '106000', 99)  # minute 60: ignored, not a 10:07 bar
        late = [event('10:06:30', '100400', 101), event('10:09:30', '100700', 107), event('10:10:30', '100800', 108)]
        self.assertEqual(future([decision, bad_clock, *late]), ('2026-11-06T10:07:00+09:00', '107', 'observed'))  # +7 min edge
        self.assertEqual(future([decision, event('10:07:00', '100500', 105), *late]),
                         ('2026-11-06T10:05:00+09:00', '105', 'observed'))  # +5 min edge, the earliest wins
        self.assertEqual(future([decision, event('10:10:30', '100800', 108)]), (None, None, 'insufficient'))

    def test_future_bar_is_observed_later_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp)/'ledger.db', Path(temp)/'observations.db'
            ledger(source,[event('10:00:30','095800',100),event('10:08:00','100600',105)])
            first=evaluate(source,out)
            self.assertEqual(first['observed'],1)
            self.assertEqual(evaluate(source,out),first)
            with sqlite3.connect(out) as db:
                row=db.execute("SELECT baseline_time,future_time,future_seen_at,change_pct,status FROM price_observations WHERE offset=0 AND symbol='K200'").fetchone()
                self.assertTrue(row[1]> '2026-11-06T10:00:30+09:00')
                self.assertTrue(row[2]>row[1])
                self.assertEqual(DecimalValue(row[3]),5)
                self.assertEqual(row[4],'observed')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM price_observations').fetchone()[0],2)

    def test_seven_etfs_are_recorded_and_missing_legacy_rows_are_classified(self):
        from etf_universe import SYMBOLS
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp)/'ledger.db', Path(temp)/'observations.db'
            first = event('10:00:30', '095800', 100)
            later = event('10:08:00', '100600', 105)
            for symbol in SYMBOLS:
                first['market_bars'][symbol] = [{'bsop_time': '095800', 'stck_prpr': '100'}]
                later['market_bars'][symbol] = [{'bsop_time': '100600', 'stck_prpr': '105'}]
            ledger(source, [first, later])
            self.assertEqual(evaluate(source, out)['observed'], 7)
            with sqlite3.connect(out) as db:
                rows = db.execute('SELECT symbol,universe,status FROM price_observations WHERE offset=0').fetchall()
            self.assertEqual({r[0] for r in rows}, set(SYMBOLS))
            self.assertTrue(all(r[1:] == ('etf7', 'observed') for r in rows))

    def test_observations_do_not_cross_universe_versions(self):
        from etf_universe import SYMBOLS
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp)/'ledger.db', Path(temp)/'observations.db'
            first = event('10:00:30', '095800', 100)
            first['market_bars']['INV'] = [{'bsop_time': '095800', 'stck_prpr': '100'}]
            later = event('10:08:00', '100600', 105)
            for symbol in SYMBOLS:
                later['market_bars'][symbol] = [{'bsop_time': '100600', 'stck_prpr': '105'}]
            ledger(source, [first, later])
            evaluate(source, out)
            with sqlite3.connect(out) as db:
                row = db.execute("SELECT universe,status FROM price_observations WHERE offset=0 AND symbol='K200'").fetchone()
            self.assertEqual(row, ('legacy2', 'insufficient'))

    def test_old_fictitious_missing_rows_are_relabelled_without_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp)/'ledger.db', Path(temp)/'observations.db'
            first = event('10:00:30', '095800', 100)
            first['market_bars']['INV'] = [{'bsop_time': '095800', 'stck_prpr': '100'}]
            ledger(source, [first])
            with sqlite3.connect(out) as db:
                db.execute('''CREATE TABLE price_observations (
                    source TEXT, offset INTEGER, symbol TEXT, decision_time TEXT, choice TEXT,
                    baseline_time TEXT, baseline_close TEXT, future_time TEXT, future_seen_at TEXT,
                    future_close TEXT, change_pct TEXT, status TEXT, PRIMARY KEY(source,offset,symbol))''')
                db.execute('INSERT INTO price_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('s',0,'LEV',first['time'],'WAIT',None,None,None,None,None,None,'insufficient'))
            self.assertEqual(evaluate(source, out), evaluate(source, out))
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute("SELECT status,universe FROM price_observations WHERE symbol='LEV'").fetchone(),
                                 ('not_recorded','legacy2'))
    def test_no_future_leak_and_missing_stays_insufficient(self):
        with tempfile.TemporaryDirectory() as temp:
            source,out=Path(temp)/'ledger.db',Path(temp)/'observations.db'
            # A future timestamp embedded in the input cannot become baseline or future evidence.
            ledger(source,[event('10:00:30','100600'),event('10:01:00','100600',105)])
            self.assertEqual(evaluate(source,out)['observed'],0)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute("SELECT status FROM price_observations WHERE offset=0 AND symbol='K200'").fetchone()[0],'insufficient')
    def test_conflicting_future_data_not_used_and_source_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            source,out=Path(temp)/'ledger.db',Path(temp)/'observations.db'
            ledger(source,[event('10:00:30','095800'),event('10:08:00','100600',105),event('10:09:00','100600',106)])
            self.assertEqual(evaluate(source,out)['observed'],0)
            with self.assertRaises(ValueError):evaluate(source,source)

class IncrementalTests(unittest.TestCase):
    """Review 4 C3: the learning watch re-evaluated the whole ledger every minute; a finished day's observations are final."""

    @staticmethod
    def stream():
        out = []
        for day, price in (('2026-11-05', 100), ('2026-11-06', 200), ('2026-11-09', 300)):
            for at, bar, p in (('10:00:30', '095800', price), ('10:06:30', '100400', price + 1), ('10:08:00', '100600', price + 5)):
                out.append({'time': f'{day}T{at}+09:00', 'event': 'decision', 'choice': 'WAIT',
                            'market_bars': {'K200': [{'bsop_time': bar, 'stck_prpr': str(p)}]}})
        return out

    @staticmethod
    def append(path, offset, item):
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?,?)', ('s', offset, 'digest', item['time'], item['event'], json.dumps(item)))

    @staticmethod
    def rows(path):
        with sqlite3.connect(path) as db:
            return db.execute('SELECT * FROM price_observations ORDER BY source, offset, symbol').fetchall()

    def test_a_round_per_new_event_ends_where_one_full_evaluation_does(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            ledger(temp / 'full.db', self.stream())
            ledger(temp / 'grown.db', [])
            for offset, item in enumerate(self.stream()):  # the watch: the ledger grows, one round per minute
                self.append(temp / 'grown.db', offset, item)
                last = evaluate(temp / 'grown.db', temp / 'rounds.db')
            self.assertEqual(last, evaluate(temp / 'full.db', temp / 'once.db'))
            self.assertEqual(last, {'observed': 3, 'insufficient': 6})
            self.assertEqual(self.rows(temp / 'rounds.db'), self.rows(temp / 'once.db'))

    def test_days_before_the_previous_recorded_day_are_not_read_again(self):
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp) / 'ledger.db', Path(temp) / 'observations.db'
            ledger(source, self.stream())
            evaluate(source, out)
            before = self.rows(out)
            with sqlite3.connect(source) as db:  # rewrite 11-05 and 11-06 (the ledger never does; this shows what is read)
                db.execute("UPDATE events_v2 SET data = replace(data, '\"stck_prpr\": \"', '\"stck_prpr\": \"9') WHERE time < '2026-11-07'")
            evaluate(source, out)
            after = self.rows(out)
            self.assertEqual([r for r in after if r[3] < '2026-11-06'], [r for r in before if r[3] < '2026-11-06'])  # final
            self.assertNotEqual([r for r in after if r[3].startswith('2026-11-06')], [r for r in before if r[3].startswith('2026-11-06')])


class UniverseTests(unittest.TestCase):
    def test_a_letter_code_riding_along_keeps_the_minute_in_learning(self):
        """Review 4 C7: CODE_PATTERN allows 0183J0; the minute was dropped as legacy_other."""
        from etf_universe import SYMBOLS
        from paper_learning import universe_for
        bars = {name: [] for name in SYMBOLS}
        self.assertEqual(universe_for({**bars, '005930': []}), 'etf7')
        self.assertEqual(universe_for({**bars, '0183J0': []}), 'etf7')
        self.assertEqual(universe_for({**bars, 'SAMSUNG': []}), 'legacy_other')


def DecimalValue(s):
    from decimal import Decimal
    return Decimal(s)

if __name__ == '__main__': unittest.main()
