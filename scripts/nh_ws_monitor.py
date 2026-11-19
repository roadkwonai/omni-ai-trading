"""Read-only NH WebSocket probe. Notifications are hints, never order confirmation."""
import argparse
import json
import re
import time
import urllib.request

from dotenv import dotenv_values
import websocket
from nh_guarded_live import ENV, _NoRedirect, _token

URLS = {'quote': 'wss://api.nhplug.com:7070/websocket',
        'paper_notice': 'wss://moapi.nhplug.com:17070/websocket'}
CHANNELS = {'quote': ('ob', '114800'), 'paper_notice': ('d2', '')}


def subscription(token, mode):
    channel, key = CHANNELS[mode]
    return {'header': {'token': token, 'tr_type': '1'}, 'body': {'tr_cd': channel, 'tr_key': key}}


def parse(message, mode):
    if not isinstance(message, dict):
        return None
    header, body = message.get('header'), message.get('body')
    if not isinstance(header, dict) or not isinstance(body, dict) or 'rsp_cd' in header or 'tr_type' in header:
        return None
    if header.get('tr_cd') != CHANNELS[mode][0]:
        return None
    if mode == 'paper_notice':
        return {'kind': 'reconcile_required'}
    if header.get('tr_key') != '114800' or body.get('code') != '114800':
        return None
    try:
        bid, ask = int(body['bid']), int(body['offer'])
        stamp = body['hotime']
        if bid <= 0 or ask < bid or not isinstance(stamp, str) or not re.fullmatch(r'\d\d:\d\d:\d\d', stamp):
            return None
    except (KeyError, ValueError, TypeError):
        return None
    return {'kind': 'quote', 'bid': bid, 'ask': ask, 'exchange_time': stamp}


def probe(mode, seconds, on_event=None):
    creds = dotenv_values(ENV, interpolate=False)
    token = _token(urllib.request.build_opener(_NoRedirect()), creds['APP_KEY'], creds['APP_SECRET'])
    sock = websocket.create_connection(URLS[mode], timeout=5, enable_multithread=True)
    acknowledged = False
    count = 0
    try:
        sock.send(json.dumps(subscription(token, mode)))
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            sock.settimeout(min(2, max(0.01, end - time.monotonic())))
            try:
                raw = sock.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                break
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            header = message.get('header') if isinstance(message, dict) else None
            if isinstance(header, dict) and ('rsp_cd' in header or 'tr_type' in header):
                if header.get('tr_cd') == CHANNELS[mode][0] and header.get('rsp_cd') == '00000':
                    acknowledged = True
                else:
                    raise ValueError('subscription rejected or unexpected acknowledgement')
                continue
            parsed = parse(message, mode)
            if acknowledged and parsed:
                count += 1
                if on_event is not None:
                    on_event(parsed)
        return {'mode': mode, 'acknowledged': acknowledged, 'valid_events': count}
    finally:
        sock.close()



def wake_on_data(event, message):
    if message.get('kind') in ('quote', 'reconcile_required'):
        event.set()


def wait_or_stop(event, delay):
    return event.wait(delay)


def watch(mode, stop, on_event):
    """Reconnect with bounded backoff; never infer execution from a notice."""
    delay = 1
    while not stop.is_set():
        try:
            result = probe(mode, 60, on_event=on_event)
            if result is None or result.get('acknowledged'):
                delay = 1
        except Exception:
            pass
        if stop.is_set():
            break
        on_event({'kind': 'disconnected'})
        wait_or_stop(stop, delay)
        delay = min(delay * 2, 30)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=tuple(URLS))
    p.add_argument('--seconds', type=int, default=10, choices=range(1, 61))
    args = p.parse_args()
    try:
        print(json.dumps(probe(args.mode, args.seconds)))
    except Exception as exc:
        print(json.dumps({'mode': args.mode, 'status': 'unavailable', 'error_type': type(exc).__name__}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
