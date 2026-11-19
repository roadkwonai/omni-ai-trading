import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from paper_online_model import train, train_jev


def row(offset, at, *, status='observed', seen=None, future=None, close='105', choice='K200', baseline=None):
    return ('source',offset,'K200',f'2026-11-06T{at}+09:00',choice,
            f'2026-11-06T{baseline or "09:58:00"}+09:00','100',
            f'2026-11-06T{future}+09:00' if future else None,
            f'2026-11-06T{seen}+09:00' if seen else None,close,status)


def create(path, rows):
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE price_observations (source,offset,symbol,decision_time,choice,
            baseline_time,baseline_close,future_time,future_seen_at,future_close,status)''')
        db.executemany('INSERT INTO price_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)',rows)


class PrequentialTests(unittest.TestCase):
    def test_jev_only_entry_uses_holding_context_for_later_price_only(self):
        from etf_universe import SYMBOLS
        from paper_learning import evaluate
        from trading_ledger import ingest
        with tempfile.TemporaryDirectory() as d:
            events,ledger,obs,model=(Path(d)/p for p in ('events.jsonl','ledger.db','obs.db','model.db'))
            records=[]
            for clock,bar,price,kind,cycle in [('10:00:00','095800','100','entry','a'*32),
                                               ('10:08:00','100600','105','holding','b'*32)]:
                at=f'2026-11-06T{clock}+09:00'
                after=f'2026-11-06T{clock[:-2]}01+09:00'
                groups={name:[{'bsop_time':bar,'stck_oprc':price,'stck_hgpr':price,
                               'stck_lwpr':price,'stck_prpr':price,'vol':'100'}] for name in SYMBOLS}
                context={'event':'market_context','time':at,'context':kind,'universe':'etf7',
                         'cycle_id':cycle,'market_bars':groups}
                if kind == 'holding': context['symbol']=SYMBOLS['K200']
                records.append(context)
                if kind == 'entry':
                    records.append({'event':'jev_shadow','time':after,'cycle_id':cycle,
                                    'jev':{'model':'jev-1.14.0','response_time':after,
                                           'candidate':{'choice':'K200','confidence':0.9}}})
            events.write_text(''.join(json.dumps(event)+'\n' for event in records))
            self.assertEqual(ingest(events,ledger),3)
            self.assertEqual(evaluate(ledger,obs)['observed'],7)
            self.assertEqual(train_jev(ledger,obs,model)['predictions'],1)
            self.assertEqual(train(obs,model)['predictions'],0)
            with sqlite3.connect(obs) as db:
                self.assertEqual(db.execute('SELECT COUNT(DISTINCT offset) FROM price_observations').fetchone()[0],1)

    def test_jev_only_jsonl_ingestion_to_prequential_research(self):
        from etf_universe import SYMBOLS
        from paper_learning import evaluate
        from trading_ledger import ingest
        with tempfile.TemporaryDirectory() as d:
            events,ledger,obs,model=(Path(d)/p for p in ('events.jsonl','ledger.db','obs.db','model.db'))
            records=[]
            for clock,bar,price,cycle in [('10:00:00','095800','100','a'*32),('10:08:00','100600','105','b'*32)]:
                at=f'2026-11-06T{clock}+09:00'
                after=f'2026-11-06T{clock[:-2]}01+09:00'
                groups={name:[{'bsop_time':bar,'stck_oprc':price,'stck_hgpr':price,
                               'stck_lwpr':price,'stck_prpr':price,'vol':'100'}] for name in SYMBOLS}
                records.extend([{'event':'market_context','time':at,'context':'entry','universe':'etf7',
                                 'cycle_id':cycle,'market_bars':groups},
                                {'event':'jev_shadow','time':after,'cycle_id':cycle,
                                 'jev':{'model':'jev-1.14.0','response_time':after,
                                        'candidate':{'choice':'K200','confidence':0.9}}}])
            events.write_text(''.join(json.dumps(event)+'\n' for event in records))
            self.assertEqual(ingest(events,ledger),4)
            evaluate(ledger,obs)
            self.assertEqual(train(obs,model)['predictions'],0)
            self.assertEqual(train_jev(ledger,obs,model)['predictions'],2)
            with sqlite3.connect(model) as db:
                self.assertEqual(db.execute('SELECT universe,alpha,beta FROM jev_predictions_latest ORDER BY jev_time').fetchall(),
                                 [('etf7_jev_only',1,1),('etf7_jev_only',2,1)])

    def test_jev_research_keeps_one_latest_run_and_leaves_old_rows_alone(self):
        from etf_universe import SYMBOLS
        from paper_learning import evaluate
        from trading_ledger import ingest
        with tempfile.TemporaryDirectory() as d:
            events,ledger,obs,model=(Path(d)/p for p in ('events.jsonl','ledger.db','obs.db','model.db'))
            with sqlite3.connect(model) as db:  # rows an earlier version kept for every run
                db.execute('CREATE TABLE jev_predictions (run_digest TEXT, source TEXT, offset INTEGER, symbol TEXT)')
                db.execute("INSERT INTO jev_predictions VALUES ('old','s',1,'K200')")
            def cycle(clock,bar,price,cycle_id):
                at,after=f'2026-11-06T{clock}+09:00',f'2026-11-06T{clock[:-2]}01+09:00'
                groups={name:[{'bsop_time':bar,'stck_oprc':price,'stck_hgpr':price,'stck_lwpr':price,'stck_prpr':price,'vol':'1'}] for name in SYMBOLS}
                return [{'event':'market_context','time':at,'context':'entry','universe':'etf7','cycle_id':cycle_id,'market_bars':groups},
                        {'event':'jev_shadow','time':after,'cycle_id':cycle_id,
                         'jev':{'model':'jev-1.14.0','response_time':after,'candidate':{'choice':'K200','confidence':0.9}}}]
            runs=[]
            for n,(clock,bar,price) in enumerate([('10:00:00','095800','100'),('10:08:00','100600','105'),('10:16:00','101400','103')]):
                with events.open('a') as f: f.write(''.join(json.dumps(e)+'\n' for e in cycle(clock,bar,price,'%032x'%(n+1))))
                ingest(events,ledger); evaluate(ledger,obs)
                runs.append(train_jev(ledger,obs,model))
                with sqlite3.connect(model) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM jev_predictions_latest').fetchone()[0],runs[-1]['predictions'])
                    self.assertEqual(db.execute('SELECT DISTINCT run_digest FROM jev_predictions_latest').fetchall(),[(runs[-1]['run_digest'],)])
            self.assertEqual([r['predictions'] for r in runs],[1,2,3])
            with sqlite3.connect(model) as db:
                self.assertEqual(db.execute('SELECT * FROM jev_predictions').fetchall(),[('old','s',1,'K200')])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM jev_runs').fetchone()[0],3)

    def test_jev_only_observation_cannot_enter_decision_training_even_with_choice(self):
        with tempfile.TemporaryDirectory() as d:
            src,out=Path(d)/'observations.db',Path(d)/'model.db'
            create(src,[row(1,'10:00:00',choice='K200',status='insufficient')])
            with sqlite3.connect(src) as db:
                db.execute('ALTER TABLE price_observations ADD COLUMN universe TEXT')
                db.execute("UPDATE price_observations SET universe='etf7_jev_only'")
            self.assertEqual(train(src,out)['predictions'],0)

    def test_jev_only_context_is_isolated_and_only_prior_mature_results_train(self):
        with tempfile.TemporaryDirectory() as d:
            ledger, src, out = (Path(d)/p for p in ('ledger.db','observations.db','model.db'))
            create(src, [row(1,'09:50:00',choice='K200',future='09:55:30',seen='09:58:00',baseline='09:48:00'),
                         row(3,'10:00:30',choice=None,future='10:06:00',seen='10:08:00',baseline='09:58:00'),
                         row(5,'10:08:00',choice=None,status='insufficient',baseline='10:06:00')])
            with sqlite3.connect(src) as db:
                db.execute('ALTER TABLE price_observations ADD COLUMN universe TEXT')
                db.execute("UPDATE price_observations SET universe='etf7' WHERE offset=1")
                db.execute("UPDATE price_observations SET universe='etf7_jev_only' WHERE offset IN (3,5)")
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, time TEXT, event TEXT, data TEXT)')
                for offset,clock,kind in [(1,'09:50:00','decision'),(3,'10:00:30','market_context'),(5,'10:08:00','market_context')]:
                    cycle = str(offset)*32
                    first = {'event':kind,'cycle_id':cycle,'time':f'2026-11-06T{clock}+09:00'}
                    if kind == 'decision': first['choice'] = 'K200'
                    else: first.update(universe='etf7',context='entry')
                    jev = {'event':'jev_shadow','jev_choice':'K200','jev_model':'jev-1.14.0',
                           'jev_confidence':0.9,'cycle_id':cycle}
                    if kind == 'decision': jev['openai_choice'] = 'K200'
                    jev_clock = '10:08:10' if offset == 5 else clock
                    if kind == 'market_context':
                        jev['jev_response_time'] = f'2026-11-06T{jev_clock}+09:00'
                        jev['time'] = jev['jev_response_time']
                    db.executemany('INSERT INTO events_v2 VALUES (?,?,?,?,?)',[
                        ('source',offset,f'2026-11-06T{clock}+09:00',kind,json.dumps(first)),
                        ('source',offset+1,f'2026-11-06T{jev_clock}+09:00','jev_shadow',json.dumps(jev))])
            result = train_jev(ledger, src, out)
            self.assertEqual(result['predictions'], 3)
            self.assertEqual(result, train_jev(ledger, src, out))
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT offset,universe,alpha,beta FROM jev_predictions_latest ORDER BY offset').fetchall(),
                                 [(1,'etf7',1,1),(3,'etf7_jev_only',1,1),(5,'etf7_jev_only',2,1)])
            train(src, out)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT offset FROM predictions').fetchall(), [(1,)])

    def test_jev_only_context_requires_exact_cycle_and_no_decision_claim(self):
        with tempfile.TemporaryDirectory() as d:
            ledger, src, out = (Path(d)/p for p in ('ledger.db','observations.db','model.db'))
            create(src, [row(1,'10:00:00',choice=None,status='insufficient')])
            with sqlite3.connect(src) as db:
                db.execute('ALTER TABLE price_observations ADD COLUMN universe TEXT')
                db.execute("UPDATE price_observations SET universe='etf7_jev_only'")
            first={'event':'market_context','context':'entry','universe':'etf7','cycle_id':'a'*32,'time':'2026-11-06T10:00:00+09:00'}
            second={'event':'jev_shadow','jev_choice':'K200','jev_model':'jev-1.14.0','jev_confidence':0.9,'cycle_id':'b'*32,
                    'time':'2026-11-06T10:00:00+09:00','jev_response_time':'2026-11-06T10:00:00+09:00'}
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, time TEXT, event TEXT, data TEXT)')
                stamp='2026-11-06T10:00:00+09:00'
                db.executemany('INSERT INTO events_v2 VALUES (?,?,?,?,?)',[('source',1,stamp,'market_context',json.dumps(first)),('source',2,stamp,'jev_shadow',json.dumps(second))])
            self.assertEqual(train_jev(ledger,src,out)['predictions'],0)
            second['cycle_id'] = 'a'*32
            second['openai_choice'] = 'K200'
            with sqlite3.connect(ledger) as db:
                db.execute('UPDATE events_v2 SET data=? WHERE offset=2',(json.dumps(second),))
            self.assertEqual(train_jev(ledger,src,out)['predictions'],0)
            del second['openai_choice']
            second['jev_response_time'] = '2026-11-06T10:00:01+09:00'
            with sqlite3.connect(ledger) as db:
                db.execute('UPDATE events_v2 SET data=? WHERE offset=2',(json.dumps(second),))
            self.assertEqual(train_jev(ledger,src,out)['predictions'],0)
            second['jev_response_time'] = stamp
            with sqlite3.connect(ledger) as db:
                db.execute('UPDATE events_v2 SET data=? WHERE offset=2',(json.dumps(second),))
                db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?)',('source',3,stamp,'market_context',json.dumps(first)))
            self.assertEqual(train_jev(ledger,src,out)['predictions'],0)

    def test_jev_only_label_requires_five_minutes_after_jev_not_context(self):
        with tempfile.TemporaryDirectory() as d:
            ledger,src,out=(Path(d)/p for p in ('ledger.db','observations.db','model.db'))
            create(src,[row(1,'10:00:00',choice=None,future='10:05:00',seen='10:07:00'),
                        row(3,'10:08:00',choice=None,status='insufficient',baseline='10:06:00')])
            with sqlite3.connect(src) as db:
                db.execute('ALTER TABLE price_observations ADD COLUMN universe TEXT')
                db.execute("UPDATE price_observations SET universe='etf7_jev_only'")
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, time TEXT, event TEXT, data TEXT)')
                for offset,context_time,jev_time in [(1,'10:00:00','10:00:30'),(3,'10:08:00','10:08:00')]:
                    cycle=str(offset)*32; at=f'2026-11-06T{context_time}+09:00'; jt=f'2026-11-06T{jev_time}+09:00'
                    first={'event':'market_context','time':at,'context':'entry','universe':'etf7','cycle_id':cycle}
                    second={'event':'jev_shadow','time':jt,'jev_response_time':jt,'jev_choice':'K200',
                            'jev_model':'jev-1.14.0','jev_confidence':0.9,'cycle_id':cycle}
                    db.executemany('INSERT INTO events_v2 VALUES (?,?,?,?,?)',[
                        ('source',offset,at,'market_context',json.dumps(first)),
                        ('source',offset+1,jt,'jev_shadow',json.dumps(second))])
            train_jev(ledger,src,out)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT alpha,beta FROM jev_predictions_latest WHERE offset=3').fetchone(),(1,1))

    def test_seven_etf_training_does_not_use_legacy_candidate_universe(self):
        with tempfile.TemporaryDirectory() as d:
            src,out=Path(d)/'observations.db',Path(d)/'model.db'
            create(src,[row(1,'10:00:00',future='10:05:00',seen='10:07:00'),
                        row(2,'10:08:00',status='insufficient',baseline='10:06:00')])
            with sqlite3.connect(src) as db:
                db.execute('ALTER TABLE price_observations ADD COLUMN universe TEXT')
                db.execute("UPDATE price_observations SET universe='legacy2' WHERE offset=1")
                db.execute("UPDATE price_observations SET universe='etf7' WHERE offset=2")
            train(src,out)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT universe,alpha,beta FROM predictions WHERE offset=2').fetchone(), ('etf7',1,1))
                self.assertEqual(db.execute('SELECT version FROM runs').fetchone()[0], 'beta-direction-prequential-v2')

    def test_jev_seven_etf_does_not_inherit_legacy_successes(self):
        with tempfile.TemporaryDirectory() as d:
            ledger,src,out=(Path(d)/p for p in ('ledger.db','observations.db','model.db'))
            create(src,[row(1,'10:00:00',choice='WAIT',future='10:05:00',seen='10:07:00'),
                        row(3,'10:08:00',choice='WAIT',status='insufficient',baseline='10:06:00')])
            with sqlite3.connect(src) as db:
                db.execute('ALTER TABLE price_observations ADD COLUMN universe TEXT')
                db.execute("UPDATE price_observations SET universe='legacy2' WHERE offset=1")
                db.execute("UPDATE price_observations SET universe='etf7' WHERE offset=3")
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, time TEXT, event TEXT, data TEXT)')
                for offset,clock in [(1,'10:00:00'),(3,'10:08:00')]:
                    decision={'event':'decision','choice':'WAIT','cycle_id':str(offset)*32}
                    jev={'event':'jev_shadow','openai_choice':'WAIT','jev_choice':'K200','jev_model':'jev-1.14.0',
                         'jev_confidence':0.9,'cycle_id':decision['cycle_id']}
                    for shift,event in enumerate((decision,jev)):
                        db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?)',('source',offset+shift,
                            f'2026-11-06T{clock}+09:00',event['event'],json.dumps(event)))
            train_jev(ledger,src,out)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT universe,alpha,beta FROM jev_predictions_latest WHERE offset=3').fetchone(),
                                 ('etf7',1,1))

    def test_old_model_predictions_remain_readable_when_universe_is_added(self):
        with tempfile.TemporaryDirectory() as d:
            src,out=Path(d)/'observations.db',Path(d)/'model.db'
            create(src,[row(1,'10:00:00',status='insufficient')])
            with sqlite3.connect(out) as db:
                db.execute('CREATE TABLE runs (digest TEXT PRIMARY KEY, version TEXT, source_rows INTEGER, predictions INTEGER)')
                db.execute('''CREATE TABLE predictions (run_digest TEXT, source TEXT, offset INTEGER, symbol TEXT,
                    decision_time TEXT, alpha INTEGER, beta INTEGER, probability REAL,
                    PRIMARY KEY(run_digest,source,offset,symbol))''')
                db.execute("INSERT INTO runs VALUES ('old','beta-direction-prequential-v1',1,1)")
                db.execute("INSERT INTO predictions VALUES ('old','source',1,'K200','2026-11-06T09:00:00+09:00',1,1,0.5)")
            train(src,out)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute("SELECT universe FROM predictions WHERE run_digest='old'").fetchone(),(None,))
                self.assertEqual(db.execute('SELECT COUNT(*) FROM predictions').fetchone()[0],2)
    def test_jev_wait_candidate_is_prequential_and_separate(self):
        with tempfile.TemporaryDirectory() as d:
            ledger, src, out = (Path(d)/p for p in ('ledger.db','observations.db','model.db'))
            create(src, [row(1, '10:00:00', choice='WAIT', future='10:05:00', seen='10:07:00'),
                         row(3, '10:07:00', choice='WAIT', status='insufficient', baseline='10:05:00'),
                         row(5, '10:08:00', choice='WAIT', status='insufficient', baseline='10:06:00'),
                         row(7, '10:09:00', choice='WAIT', status='insufficient', baseline='10:07:00')])
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, time TEXT, event TEXT, data TEXT)')
                for offset, clock, choice in [(1,'10:00:00','K200'),(3,'10:07:00','K200'),
                                              (5,'10:08:00','K200'),(7,'10:09:00','WAIT')]:
                    for n, event in enumerate(({'event':'decision','choice':'WAIT'},
                           {'event':'jev_shadow','openai_choice':'WAIT','jev_choice':choice,
                            'jev_model':'jev-1.14.0','jev_confidence':0.9})):
                        stamp=f'2026-11-06T{clock}+09:00'
                        db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?)',('source',offset+n,stamp,event['event'],json.dumps(event)))
            result=train_jev(ledger,src,out)
            self.assertEqual(result,train_jev(ledger,src,out))
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT offset,alpha,beta FROM jev_predictions_latest ORDER BY offset').fetchall(),
                                 [(1,1,1),(3,1,1),(5,2,1)])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM jev_runs').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT DISTINCT match_kind FROM jev_predictions_latest').fetchall(), [('inferred_adjacent',)])
                self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='predictions'").fetchone())

    def test_jev_requires_matched_choice_cycle_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            ledger, src, out = (Path(d)/p for p in ('ledger.db','observations.db','model.db'))
            create(src, [row(1,'10:00:00',choice='WAIT',status='insufficient')])
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, time TEXT, event TEXT, data TEXT)')
                stamp='2026-11-06T10:00:00+09:00'
                a={'event':'decision','choice':'WAIT','cycle_id':'a'*32}
                b={'event':'jev_shadow','openai_choice':'WAIT','jev_choice':'K200','jev_model':'jev-1.14.0','jev_confidence':0.9,'cycle_id':'b'*32}
                db.executemany('INSERT INTO events_v2 VALUES (?,?,?,?,?)', [('source',1,stamp,'decision',json.dumps(a)),('source',2,stamp,'jev_shadow',json.dumps(b))])
            self.assertEqual(train_jev(ledger,src,out)['predictions'],0)

    def test_outcome_before_five_minutes_from_jev_does_not_train(self):
        with tempfile.TemporaryDirectory() as d:
            ledger, src, out = (Path(d)/p for p in ('ledger.db','observations.db','model.db'))
            create(src, [row(1,'10:00:00',choice='WAIT',future='10:05:00',seen='10:07:00'),
                         row(3,'10:08:00',choice='WAIT',status='insufficient',baseline='10:06:00')])
            with sqlite3.connect(ledger) as db:
                db.execute('CREATE TABLE events_v2 (source TEXT, offset INTEGER, time TEXT, event TEXT, data TEXT)')
                for offset, clock in [(1,'10:00:00'),(3,'10:08:00')]:
                    decision={'event':'decision','choice':'WAIT','cycle_id':'a'*32 if offset == 1 else 'b'*32}
                    jev={'event':'jev_shadow','openai_choice':'WAIT','jev_choice':'K200','jev_model':'jev-1.14.0',
                         'jev_confidence':0.9,'cycle_id':decision['cycle_id']}
                    db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?)',('source',offset,f'2026-11-06T{clock}+09:00','decision',json.dumps(decision)))
                    jev_clock='10:00:30' if offset == 1 else '10:08:00'
                    db.execute('INSERT INTO events_v2 VALUES (?,?,?,?,?)',('source',offset+1,f'2026-11-06T{jev_clock}+09:00','jev_shadow',json.dumps(jev)))
            train_jev(ledger,src,out)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT alpha,beta,match_kind FROM jev_predictions_latest WHERE offset=3').fetchone(),(1,1,'cycle_id'))
    def test_only_previously_matured_label_affects_prediction(self):
        with tempfile.TemporaryDirectory() as d:
            src,out=Path(d)/'observations.db',Path(d)/'model.db'
            create(src,[row(1,'10:00:00',future='10:05:00',seen='10:07:00'),
                        row(2,'10:07:00',status='insufficient',baseline='10:05:00'),
                        row(3,'10:08:00',status='insufficient',baseline='10:06:00')])
            result=train(src,out);self.assertEqual(result,train(src,out))
            with sqlite3.connect(out) as db:
                values=db.execute('SELECT offset,alpha,beta FROM predictions ORDER BY offset').fetchall()
                self.assertEqual(values,[(1,1,1),(2,1,1),(3,2,1)])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],1)

    def test_early_seen_invalid_future_flat_and_missing_do_not_train(self):
        with tempfile.TemporaryDirectory() as d:
            src,out=Path(d)/'observations.db',Path(d)/'model.db'
            create(src,[row(1,'10:00:00',future='10:05:00',seen='10:05:30'),
                        row(2,'10:10:00',future='10:14:00',seen='10:20:00',baseline='10:08:00'),
                        row(3,'10:20:00',future='10:25:00',seen='10:27:00',baseline='10:18:00',close='100'),
                        row(4,'10:30:00',status='insufficient',baseline='10:28:00')])
            train(src,out)
            with sqlite3.connect(out) as db:
                self.assertEqual(db.execute('SELECT alpha,beta FROM predictions WHERE offset=4').fetchone(),(1,1))
            with self.assertRaises(ValueError):train(src,src)

    def test_changed_source_gets_new_versioned_run(self):
        with tempfile.TemporaryDirectory() as d:
            src,out=Path(d)/'observations.db',Path(d)/'model.db';create(src,[row(1,'10:00:00',status='insufficient')])
            first=train(src,out)
            with sqlite3.connect(src) as db:db.execute("UPDATE price_observations SET choice='WAIT'")
            second=train(src,out);self.assertNotEqual(first['run_digest'],second['run_digest'])
            with sqlite3.connect(out) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],2)

if __name__=='__main__': unittest.main()
