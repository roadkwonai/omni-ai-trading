"""Every step-by-step flow is drawn with Archify (user rule 2026-11-18): each spec must pass Archify's own validation
(six columns, labels that fit, a main path that never runs backward), or the screen silently falls back to Graphviz."""
import shutil

import pytest

import guides
import longterm_account_view as AV
import longterm_dca_view as DV
import longterm_inputs_guide as IG
import longterm_strategy_guide as SG
import oversold_flow as OF
import strategy_map as smap
import us_ideas_view as UV
from strategy_settings import DEFAULTS

pytestmark = pytest.mark.skipif(not shutil.which('node') or not smap.ARCHIFY.exists(), reason='node or Archify not installed')


def specs():
    v = guides.values()
    yield from ((f'jev-{k}', build(v)) for k, build in guides.FLOWS.items())
    yield from (('oversold', OF.flow_spec(DEFAULTS)), ('rebound', OF.rebound_spec(DEFAULTS)), ('loop', OF.loop_spec(DEFAULTS)))
    yield from (('inputs', IG.data_flow()), ('time', IG.time_flow()), ('dca-day', IG.day_flow()), ('structure', SG.structure_flow()),
                ('day', SG.day_flow()), ('dca', DV.flow_spec()), ('ideas', UV.flow_spec()))
    for mode in ('실주문', '계획만'):
        yield f'account-{mode}', AV.flow_spec({'mode': mode}, '여운봉 저수지 매매법', '조정', '실행 중')


@pytest.mark.parametrize('key,spec', list(specs()), ids=lambda x: x if isinstance(x, str) else '')
def test_the_flow_passes_archify_validation(key, spec, tmp_path):
    assert smap.render(spec, tmp_path, kind=spec['diagram_type'], prefix='t').exists()
