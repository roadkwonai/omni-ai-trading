from execution_comparison import compare


def fills(buy, sell, pnl):
    return [{'symbol': '466100', 'side': 'BUY', 'qty': 24, 'price': buy, 'time': '2026-11-27T09:01:00+09:00'},
            {'symbol': '466100', 'side': 'SELL', 'qty': 24, 'price': sell, 'pnl': pnl,
             'time': '2026-11-27T09:03:00+09:00'}]


def test_paper_and_live_record_gross_price_differences():
    live = [{**row, 'path': '미러'} for row in fills(20200, 21100, 21600)]
    books = {'paper': {'fills': fills(20600, 20221, -9096)}, 'live-1': {'fills': live}}
    (case,) = compare(books)
    assert (case['buy_price_difference'], case['sell_price_difference'], case['gross_pnl_difference']) == (-400, 879, 30696)
    assert case['costs'] == 'excluded'
    assert 'submission' in case['live_time_note']


def test_skip_ambiguous_or_inconsistent_positions():
    paper = fills(20600, 20221, -9096)
    live = [{**row, 'path': '미러'} for row in fills(20200, 21100, 21600)]
    assert compare({'paper': {'fills': paper + [paper[0]]}, 'live-1': {'fills': live}}) == []
    live[1]['pnl'] = 100
    assert compare({'paper': {'fills': paper}, 'live-1': {'fills': live}}) == []
    live[1]['pnl'] = 21600
    for row in live:
        row['path'] = '장기투자'
    assert compare({'paper': {'fills': paper}, 'live-2': {'fills': live}}) == []


def test_comparison_is_available_in_next_day_journal_context():
    import trading_journal
    live = [{**row, 'path': '미러'} for row in fills(20200, 21100, 21600)]
    books = {'paper': {'fills': fills(20600, 20221, -9096)}, 'live-1': {'fills': live}}
    comparisons = compare(books)
    result = trading_journal._brief([{'day': '2026-11-27', 'books': books, 'execution_comparisons': comparisons}])
    assert result['sessions'][0]['execution_comparisons'][0]['gross_pnl_difference'] == 30696
    assert 'submission times' in result['about']
