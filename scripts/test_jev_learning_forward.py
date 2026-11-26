from datetime import datetime

from jev_learning_forward import CASH, FEE, LIMITS, actions, requests_for_frame
from jev_learning_virtual import replay


def quote(clock, bid, ask):
    stamp = f'2026-11-26T{clock}+09:00'
    return {'id': clock, 'at': stamp, 'source_at': stamp, 'received_at': stamp, 'symbol': '121600', 'bid': bid, 'ask': ask}


def test_experience_is_only_model_input_difference_and_live_position_is_removed():
    state = {'position': {'quantity': 7}, 'own_track_record': {'paper_trades': 10}, 'news': ['archived headline']}
    at = datetime.fromisoformat('2026-11-26T09:10:00+09:00')
    pair, image = requests_for_frame({'capture': {'state': state}, 'quote': quote('09:12:00', 1000, 1001),
                                      'model': 'jev-1.14.0', 'series': {'121600': [(at, 1000)]}}, {'version': 'test'})
    assert image.startswith(b'\x89PNG')
    assert pair['baseline']['questions'] == pair['experience']['questions']
    assert 'position' not in pair['baseline']['state']
    assert pair['experience']['state']['own_track_record'] == state['own_track_record']
    with_experience = pair['experience']['state'].copy()
    assert with_experience.pop('validated_experience') == {'version': 'test'}
    assert with_experience == pair['baseline']['state']
    assert state['position']['quantity'] == 7


def test_two_model_answers_use_independent_books_and_one_common_exit():
    quotes = {'121600': [quote('09:12:00', 999, 1000), quote('09:13:00', 1021, 1022)]}
    predictions = {'09:12:00': {'baseline': {'choice': 'WAIT', 'confidence': .7},
                                'experience': {'choice': '121600', 'confidence': .7}}}
    result = replay(actions(quotes, predictions), cash=CASH, limits=LIMITS, fee=FEE)
    assert result['baseline']['fills'] == 0
    assert result['experience']['fills'] == 2
    assert 0 < result['experience']['realized_krw'] < 10_500
    assert result['experience']['sampled_max_drawdown_krw'] > 0
