"""Operator action: resume NH while preserving the prior halt on known other brokers."""
import json
from src.live.halt import read_halt, trip_halt, clear_halt, halt_flag_set, broker_halt_path
from src.live.paths import live_root
from src.trading.profiles import BUILTIN_PROFILES
from src.config.loader import load_agent_config
from src.config.schema import live_broker_key_for_entry


def resume_nh(expected_global):
    if read_halt()!=expected_global:raise ValueError('global halt changed; activation stopped')
    brokers={p.connector for p in BUILTIN_PROFILES}
    brokers.update(p.name for p in live_root().iterdir() if p.is_dir())
    for name,cfg in load_agent_config().mcp_servers.items():
        broker=live_broker_key_for_entry(name,cfg)
        if broker:brokers.add(broker)
    brokers.discard('nhplug')
    if read_halt('nhplug') is not None:raise ValueError('NH-specific halt remains active')
    created=[]
    for broker in sorted(brokers):
        if not broker_halt_path(broker).exists():
            trip_halt('cli','Preserve existing global pause while enabling requested NH-only trading',broker)
            created.append(broker)
    audit=live_root()/'nh-activation.json'
    audit.write_text(json.dumps({'prior_global_halt':expected_global,'preserved_brokers':sorted(brokers),'created_broker_halts':created},ensure_ascii=False))
    audit.chmod(0o600)
    if read_halt()!=expected_global:raise ValueError('global halt changed; activation stopped')
    clear_halt()
    if halt_flag_set('nhplug') or any(not halt_flag_set(b) for b in brokers):raise RuntimeError('scope activation verification failed')
    return {'nh_resumed':True,'other_brokers_halted':len(brokers)}
