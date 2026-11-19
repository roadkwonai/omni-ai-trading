from unittest.mock import patch

import guide_ui


def test_an_archify_frame_follows_its_content_height():
    """The page is as tall as its diagram (strategy_map.FRAME_CSS), so the frame takes its content height: no inner scroll."""
    with patch.object(guide_ui.st, 'iframe') as frame:
        guide_ui.archify_frame('/x/guide-jev-live.html', '실계좌 주문 경로')
    assert 'height' not in frame.call_args.kwargs


def test_a_guide_diagram_keeps_its_own_size_within_a_cap():
    """2026-11-19: stretched to the pop-up width, tall flows (낙폭과대 FLOW · 안전장치 · 시장 국면) were screens high."""
    with patch.object(guide_ui.st, 'graphviz_chart') as chart:
        guide_ui.diagram('digraph { rankdir=TB; a -> b }')
    src = chart.call_args.args[0]
    assert src.startswith('digraph { size="14,9!";') and chart.call_args.kwargs['width'] == 'content'
