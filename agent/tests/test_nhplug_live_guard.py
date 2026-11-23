from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.tools.nhplug_live_guard import LiveOrderBlocked, LiveOrderGuard

A = '12345678901'
NOW = datetime(2026, 11, 6, 11, 0, 10, tzinfo=ZoneInfo('Asia/Seoul'))
BUY = {'act_no': A, 'iem_cd': '114800', 'orr_qty': 1, 'orr_pr': 1400, 'nmn_pr_tp_cd': '01',
       'rmt_mkt_cd': 'KRX', 'sor_mkt_sli_yn': 'N', 'orr_cnd_dit_cd': '00', 'ssl_nmn_pr_dit_cd': '00'}
ACCOUNTS = {'rsp_cd':'00000','Output_0':[{'acct_no':A,'acct_type':'01'}]}
PNL = {'rsp_cd':'00166','rsp_msg':'조회가 완료되었습니다.', 'Output_0':{'sby_pls_amt':0,'sdr_xps':0,'eal_pls_amt':0}}
HISTORY = {'_pagination_complete':True, '_account':A, '_day':'20261106', 'rsp_cd':'00166',
           'rsp_msg':'조회가 완료되었습니다.', 'Output_0':[]}
BALANCE = {'_pagination_complete':True, '_account':A, 'rsp_cd':'00166', 'rsp_msg':'조회가 완료되었습니다.',
           'Output_0':{'orr_pbl_amt1':1000000,'tot_aet_amt':1000000,'tot_byn_amt':0}, 'Output_1':[]}

@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TRADING_HOME', str(tmp_path / 'runtime'))

def quote(clock):
    return {'status':'ok','data':{'rsp_cd':'00000','Output_0':{'iem_cd':'114800','market_status':'1','hoga_bsop_hour':clock,'askp':1400,'bidp':1399}}}

def reserve(g, op='krstockOrderCashBuy', fields=None, **kwargs):
    options = {'now':NOW,'accounts':ACCOUNTS,'history':HISTORY,'pnl':PNL,
               'previous_quote':quote('11:00:07'),'quote':quote('11:00:09'),'balance':BALANCE}
    options.update(kwargs)
    if op == 'krstockOrderCashSell' and 'balance' not in kwargs:
        options['balance'] = {**BALANCE, 'Output_1':[{'iem_cd':'114800','rsdl_qty':1}]}
    return g.reserve(op, fields or BUY, **options)

def complete(**kwargs):
    row = {'itg_orr_no':42,'iem_cd':'114800','orr_qty':1,'tot_cns_qty':1,'ny_cns_qty':0,
           'can_qty':0,'sby_dit_cd_nm':'현금매수'}
    row.update(kwargs)
    return {**HISTORY, 'Output_0':[row]}

def test_loss_latches_and_stays_halted_until_next_day(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    pnl={**PNL, 'Output_0':{'sby_pls_amt':-19999,'sdr_xps':1,'eal_pls_amt':0}}
    with pytest.raises(LiveOrderBlocked, match='latch'): reserve(guard,pnl=pnl)
    with pytest.raises(LiveOrderBlocked, match='latch'): reserve(LiveOrderGuard(A,tmp_path))

def test_uncertain_submission_blocks_restart_and_duplicate(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    intent=reserve(guard)
    with pytest.raises(LiveOrderBlocked, match='uncertain'): guard.acknowledge(intent,{})
    with pytest.raises(LiveOrderBlocked): reserve(LiveOrderGuard(A,tmp_path),fields={**BUY,'orr_pr':1401})

def test_acknowledged_requires_matching_terminal_reconciliation(tmp_path):
    guard=LiveOrderGuard(A,tmp_path); intent=reserve(guard)
    guard.acknowledge(intent,{'rsp_cd':'00048','rsp_msg':'매수주문이 완료되었습니다', 'Output_0':{'mkt_orr_no':42}})
    with pytest.raises(LiveOrderBlocked): guard.reconcile(intent,complete(ny_cns_qty=1,tot_cns_qty=0))
    with pytest.raises(LiveOrderBlocked): guard.reconcile(intent,complete(sby_dit_cd_nm='현금매도'))
    assert guard.reconcile(intent,complete()) == {'filled':1,'cancelled':0}
    sell={**BUY, 'orr_pr':1399}
    with pytest.raises(LiveOrderBlocked):
        reserve(guard,'krstockOrderCashSell',sell,sellable={'_account':A,'_symbol':'114800','rsp_cd':'error','sll_pbl_qty':1})
    assert reserve(guard,'krstockOrderCashSell',sell,sellable={'_account':A,'_symbol':'114800','rsp_cd':'XA102','sll_pbl_qty':1})

def test_rejects_wrong_symbol_and_incomplete_history(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    with pytest.raises(LiveOrderBlocked): reserve(guard,fields={**BUY,'iem_cd':'005930'})
    with pytest.raises(LiveOrderBlocked): reserve(guard,history={**HISTORY,'_pagination_complete':False})
    with pytest.raises(LiveOrderBlocked): reserve(guard,history={**HISTORY,'rsp_cd':'ERROR'})
    with pytest.raises(LiveOrderBlocked): reserve(guard,quote=quote('10:55:00'))
    with pytest.raises(LiveOrderBlocked): reserve(guard,balance={**BALANCE,'_pagination_complete':False})
    with pytest.raises(LiveOrderBlocked): reserve(guard,balance={**BALANCE,'rsp_cd':'ERROR'})
    with pytest.raises(LiveOrderBlocked): reserve(guard,balance={**BALANCE,'Output_1':[{'rsdl_qty':-1}]})
    with pytest.raises(LiveOrderBlocked): reserve(guard,balance={**BALANCE,'Output_0':{'orr_pbl_amt1':100,'tot_aet_amt':100}})
    with pytest.raises(LiveOrderBlocked): reserve(guard,fields={**BUY,'orr_pr':1401})
    with pytest.raises(LiveOrderBlocked): reserve(guard,fields={**BUY,'orr_amt':1000000})

def test_simultaneous_reservations_only_one_succeeds(tmp_path):
    def attempt():
        try:
            reserve(LiveOrderGuard(A,tmp_path)); return True
        except LiveOrderBlocked:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: attempt(), range(2))) == 1

def test_cancel_reserves_only_known_order_and_reconciles_terminal(tmp_path):
    guard=LiveOrderGuard(A,tmp_path); intent=reserve(guard)
    guard.acknowledge(intent,{'rsp_cd':'00048','rsp_msg':'매수주문이 완료되었습니다', 'Output_0':{'mkt_orr_no':42}})
    cancel={'act_no':A,'iem_cd':'114800','org_mkt_orr_no':42,'all_pat_dit_cd':'1'}
    cancellation=reserve(guard,'krstockOrderCancel',cancel,history=complete(tot_cns_qty=0,ny_cns_qty=1))
    guard.acknowledge(cancellation,{'rsp_cd':'00192','rsp_msg':'취소주문이 완료되었습니다', 'Output_0':{'mkt_orr_no':43}})
    outcome=complete(tot_cns_qty=0,can_qty=1)
    assert guard.reconcile(intent,outcome) == {'filled':0,'cancelled':1}
    with pytest.raises(LiveOrderBlocked): reserve(guard,fields={**BUY,'orr_pr':1402})

def test_adopt_external_cancel_requires_exact_live_order(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    pending=complete(tot_cns_qty=0,ny_cns_qty=1,orr_rjt_rsn_cd_nm='정상')
    pending['Output_0'].append({'orr_qty':1,'tot_cns_qty':0,'ny_cns_qty':0,'can_qty':0,'orr_rjt_rsn_cd_nm':'확인'})
    intent=guard.adopt_order(now=NOW,history=pending,order_number=42)
    with pytest.raises(LiveOrderBlocked):guard.adopt_order(now=NOW,history=pending,order_number=42)
    cancel={'act_no':A,'iem_cd':'114800','org_mkt_orr_no':42,'all_pat_dit_cd':'1'}
    with pytest.raises(LiveOrderBlocked):reserve(guard,'krstockOrderCancel',cancel,history=complete())
    cancel_intent=reserve(guard,'krstockOrderCancel',cancel,history=pending)
    guard.acknowledge(cancel_intent,{'rsp_cd':'00192','rsp_msg':'취소주문이 완료되었습니다','Output_0':{'mkt_orr_no':43}})
    with pytest.raises(LiveOrderBlocked):guard.reconcile(intent,complete(tot_cns_qty=0,can_qty=0,orr_rjt_rsn_cd_nm='확인'))
    assert guard.reconcile(intent,complete(tot_cns_qty=0,can_qty=1))['cancelled']==1

def test_uncertain_cancel_only_resolves_from_original_terminal_row(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    pending=complete(tot_cns_qty=0,ny_cns_qty=1,orr_rjt_rsn_cd_nm='정상')
    intent=guard.adopt_order(now=NOW,history=pending,order_number=42)
    cancel={'act_no':A,'iem_cd':'114800','org_mkt_orr_no':42,'all_pat_dit_cd':'1'}
    cancel_intent=reserve(guard,'krstockOrderCancel',cancel,history=pending)
    with pytest.raises(LiveOrderBlocked):guard.reconcile(intent,pending)
    with guard._db() as db:assert db.execute('select state from live_intents where fingerprint=?',(cancel_intent,)).fetchone()[0]=='uncertain'
    guard.reconcile(intent,complete(tot_cns_qty=0,can_qty=1))
    with guard._db() as db:assert db.execute('select state from live_intents where fingerprint=?',(cancel_intent,)).fetchone()[0]=='terminal'

@pytest.mark.parametrize('update',[{'iem_cd':'005930'},{'sby_dit_cd_nm':'현금매도'},{'orr_qty':2},{'ny_cns_qty':0}])
def test_adopt_rejects_wrong_order(tmp_path,update):
    guard=LiveOrderGuard(A,tmp_path)
    row={'itg_orr_no':42,'iem_cd':'114800','orr_qty':1,'tot_cns_qty':0,'ny_cns_qty':1,'can_qty':0,'sby_dit_cd_nm':'현금매수','orr_rjt_rsn_cd_nm':'정상'}
    row.update(update)
    with pytest.raises(LiveOrderBlocked):guard.adopt_order(now=NOW,history={**HISTORY,'Output_0':[row]},order_number=42)

def test_loss_observation_latches_even_when_quotes_are_stale(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    pnl={**PNL, 'Output_0':{'sby_pls_amt':-20000,'sdr_xps':0,'eal_pls_amt':0}}
    with pytest.raises(LiveOrderBlocked): reserve(guard,pnl=pnl,quote=quote('10:55:00'))
    with pytest.raises(LiveOrderBlocked,match='latch'): reserve(guard)

def test_global_halt_and_business_error_fail_closed(tmp_path):
    from src.live.halt import halt_path
    path = halt_path()
    path.parent.mkdir(parents=True)
    path.touch()
    guard = LiveOrderGuard(A, tmp_path)
    with pytest.raises(LiveOrderBlocked, match='kill switch'): reserve(guard)
    path.unlink()
    intent = reserve(guard)
    with pytest.raises(LiveOrderBlocked, match='business'):
        guard.acknowledge(intent, {'rsp_cd':'ERROR','rsp_msg':'거절', 'Output_0':{'mkt_orr_no':42}})
    with pytest.raises(LiveOrderBlocked): reserve(guard, fields={**BUY,'orr_pr':1399})

def test_adoption_persists_to_next_day_and_is_idempotent(tmp_path):
    from datetime import timedelta
    guard=LiveOrderGuard(A,tmp_path)
    balance={**BALANCE,'Output_1':[{'iem_cd':'114800','rsdl_qty':1}]}
    sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':1}
    guard.adopt_existing(now=NOW,balance=balance,sellable=sellable)
    tomorrow=NOW+timedelta(days=3)  # Friday -> next weekday
    guard.adopt_existing(now=tomorrow,balance=balance,sellable=sellable)
    with guard._db() as db:
        assert db.execute('select count(*) from live_adoptions').fetchone()[0] == 1
    history={**HISTORY,'_day':tomorrow.strftime('%Y%m%d')}
    assert reserve(guard,'krstockOrderCashSell',{**BUY,'orr_pr':1399},now=tomorrow,history=history,sellable=sellable)

def test_adoption_after_terminal_history_and_conflict_blocks(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    balance={**BALANCE,'Output_1':[{'iem_cd':'114800','rsdl_qty':1}]}
    sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':1}
    with guard._db() as db:
        db.execute('insert into live_intents values (?,?,?,?,?,?,?,?,?)',('old',guard.scope,'20261105','krstockOrderCashBuy','114800',1,123,'terminal',0))
    guard.adopt_existing(now=NOW,balance=balance,sellable=sellable)
    with guard._db() as db:
        db.execute('insert into live_intents values (?,?,?,?,?,?,?,?,?)',('pending',guard.scope,'20261106','krstockOrderCashBuy','114800',1,124,'uncertain',0))
    with pytest.raises(LiveOrderBlocked, match='unresolved'):
        guard.adopt_existing(now=NOW,balance=balance,sellable=sellable)

def test_next_day_broker_buy_ownership_allows_sell(tmp_path):
    from datetime import timedelta
    guard=LiveOrderGuard(A,tmp_path)
    with guard._db() as db:
        db.execute('insert into live_intents values (?,?,?,?,?,?,?,?,?)',('filled',guard.scope,'20261105','krstockOrderCashBuy','114800',1,123,'terminal',1))
    sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':1}
    assert reserve(guard,'krstockOrderCashSell',{**BUY,'orr_pr':1399},sellable=sellable)

def test_multiple_holdings_adoption_and_one_share_sell(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    balance={**BALANCE,'Output_1':[{'iem_cd':'114800','rsdl_qty':5}]}
    sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':4}
    guard.adopt_existing(now=NOW,balance=balance,sellable=sellable,quantity=5)
    guard.adopt_existing(now=NOW,balance=balance,sellable=sellable,quantity=5)
    with guard._db() as db: assert guard._owned(db)==5
    assert reserve(guard,'krstockOrderCashSell',{**BUY,'orr_pr':1399},sellable=sellable,balance=balance)

def test_existing_holdings_respect_capital_limit(tmp_path):
    balance={**BALANCE,'Output_1':[{'iem_cd':'114800','rsdl_qty':4}],
             'Output_0':{'orr_pbl_amt1':1000000,'tot_aet_amt':1000000,'tot_byn_amt':998601}}
    with pytest.raises(LiveOrderBlocked,match='capital'):
        reserve(LiveOrderGuard(A,tmp_path),balance=balance)
    balance['Output_0']['tot_byn_amt']=998600
    assert reserve(LiveOrderGuard(A,tmp_path),balance=balance)

def test_account_capital_above_the_old_cap_still_needs_the_cash(tmp_path):
    # user decision 2026-11-17: the account's own capital (set within its money) replaces the fixed 1,000,000
    rich={**BALANCE,'Output_0':{'orr_pbl_amt1':3000000,'tot_aet_amt':3000000,'tot_byn_amt':1500000}}
    assert reserve(LiveOrderGuard(A,tmp_path),balance=rich,capital=3000000,order_max=3000000)
    poor={**BALANCE,'Output_0':{'orr_pbl_amt1':1000,'tot_aet_amt':1501000,'tot_byn_amt':1500000}}
    with pytest.raises(LiveOrderBlocked,match='cash'):
        reserve(LiveOrderGuard(A,tmp_path),balance=poor,capital=3000000,order_max=3000000)
    with pytest.raises(LiveOrderBlocked,match='capital'):  # no capital passed: the older path keeps 1,000,000
        reserve(LiveOrderGuard(A,tmp_path),balance=rich)


def test_sell_requires_proven_live_balance(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    with guard._db() as db:
        db.execute('insert into live_adoptions values (?,?,?,?)',(guard.scope,'20261105','114800',3))
    sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':3}
    with pytest.raises(LiveOrderBlocked):
        reserve(guard,'krstockOrderCashSell',{**BUY,'orr_pr':1399},balance={**BALANCE,'Output_1':[]},sellable=sellable)

@pytest.mark.parametrize('quantity', [1, 29])
def test_sell_replacement_requires_terminal_cancel_evidence_and_is_single_use(tmp_path, quantity):
    guard=LiveOrderGuard(A,tmp_path)
    balance={**BALANCE,'Output_1':[{'iem_cd':'114800','rsdl_qty':quantity}]}
    sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':quantity}
    guard.adopt_existing(now=NOW,balance=balance,sellable=sellable,quantity=quantity)
    sell={**BUY,'orr_pr':1399,'orr_qty':quantity}
    original=reserve(guard,'krstockOrderCashSell',sell,balance=balance,sellable=sellable)
    with pytest.raises(LiveOrderBlocked):
        reserve(guard,'krstockOrderCashSell',sell,balance=balance,sellable=sellable,replacement_of=original)
    guard.acknowledge(original,{'rsp_cd':'00047','rsp_msg':'매도주문이 완료되었습니다','Output_0':{'mkt_orr_no':42}})
    with pytest.raises(LiveOrderBlocked):
        reserve(guard,'krstockOrderCashSell',sell,balance=balance,sellable=sellable,replacement_of=original)
    confirmed=complete(sby_dit_cd_nm='현금매도',orr_qty=quantity,tot_cns_qty=0,can_qty=quantity)
    ack={'itg_orr_no':43,'iem_cd':'114800','sby_dit_cd_nm':'현금매도','orr_rjt_rsn_cd_nm':'확인',
         'orr_qty':quantity,'tot_cns_qty':0,'ny_cns_qty':0,'can_qty':0}
    with pytest.raises(LiveOrderBlocked):
        guard.reconcile(original,{**HISTORY,'Output_0':[ack]})
    confirmed['Output_0'].append(ack)
    assert guard.reconcile(original,confirmed) == {'filled':0,'cancelled':quantity}
    with pytest.raises(LiveOrderBlocked,match='explicit replacement'):
        reserve(guard,'krstockOrderCashSell',{**sell,'orr_pr':1398},balance=balance,sellable=sellable)
    replacement=reserve(guard,'krstockOrderCashSell',sell,balance=balance,sellable=sellable,
                        history=confirmed,replacement_of=original)
    assert replacement != original
    with pytest.raises(LiveOrderBlocked):
        reserve(LiveOrderGuard(A,tmp_path),'krstockOrderCashSell',sell,balance=balance,sellable=sellable,replacement_of=original)


def test_filled_sell_never_authorizes_replacement(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    balance={**BALANCE,'Output_1':[{'iem_cd':'114800','rsdl_qty':2}]}
    sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':2}
    guard.adopt_existing(now=NOW,balance=balance,sellable=sellable,quantity=2)
    sell={**BUY,'orr_pr':1399}
    original=reserve(guard,'krstockOrderCashSell',sell,balance=balance,sellable=sellable)
    guard.acknowledge(original,{'rsp_cd':'00047','rsp_msg':'매도주문이 완료되었습니다','Output_0':{'mkt_orr_no':42}})
    guard.reconcile(original,complete(sby_dit_cd_nm='현금매도'))
    with pytest.raises(LiveOrderBlocked,match='verified sell cancellation'):
        reserve(guard,'krstockOrderCashSell',sell,balance=balance,sellable=sellable,replacement_of=original)


def test_aborted_replacement_preserves_original_cancel_requirement(tmp_path):
    guard=LiveOrderGuard(A,tmp_path)
    balance={**BALANCE,'Output_1':[{'iem_cd':'114800','rsdl_qty':1}]}
    sellable={'_account':A,'_symbol':'114800','rsp_cd':'00166','sll_pbl_qty':1}
    guard.adopt_existing(now=NOW,balance=balance,sellable=sellable)
    sell={**BUY,'orr_pr':1399}
    original=reserve(guard,'krstockOrderCashSell',sell,balance=balance,sellable=sellable)
    guard.acknowledge(original,{'rsp_cd':'00047','rsp_msg':'매도주문이 완료되었습니다','Output_0':{'mkt_orr_no':42}})
    guard.reconcile(original,complete(sby_dit_cd_nm='현금매도',tot_cns_qty=0,can_qty=1))
    replacement=reserve(guard,'krstockOrderCashSell',sell,balance=balance,sellable=sellable,replacement_of=original)
    guard.abort_before_send(replacement)
    with pytest.raises(LiveOrderBlocked,match='explicit replacement'):
        reserve(guard,'krstockOrderCashSell',{**sell,'orr_pr':1398},balance=balance,sellable=sellable)
    next_replacement=reserve(guard,'krstockOrderCashSell',sell,balance=balance,sellable=sellable,replacement_of=original)
    assert next_replacement != replacement
