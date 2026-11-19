"""Offline tests for the optional, explicitly guarded NH paper-order tool."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(os.environ.get('NHPLUG_MCP_SOURCE', str(Path.home() / '.vibe-trading/integrations/nhplug-mcp')))
PATCH = Path(__file__).resolve().parent / 'nhplug-deadline.patch'
JS = r'''
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync, unlinkSync, rmSync } from 'node:fs';
import { dirname } from 'node:path';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { registerTools } from './dist/tools.js';

const handlers = new Map(), schemas = new Map();
const config = {appKey:'test-key', appSecret:'test-secret',
  baseUrl:'https://moapi.nhplug.com:8443', authUrl:'https://example.invalid', enableTrading:true};
registerTools({tool:(name,description,schema,handler)=>{handlers.set(name,handler); schemas.set(name,schema);}}, config);
assert(handlers.has('call_api_guarded'));
const legacyHandlers = new Map();
const {registerTools: registerLegacy} = await import('./legacy/dist/tools.js');
registerLegacy({tool:(name,description,schema,handler)=>legacyHandlers.set(name,handler)}, config);
assert.equal(legacyHandlers.has('call_api_guarded'), false, 'old server cannot silently omit the deadline');
const guarded = handlers.get('call_api_guarded');
const input = {iem_cd:'069500', act_no:'12345678901', orr_qty:1, orr_pr:70000};
const canonical = JSON.stringify({input:Object.fromEntries(Object.entries(input).sort()), operationId:'krstockOrderCashBuy'});
const digest = createHash('sha256').update(canonical, 'utf8').digest('hex');
assert.equal(digest, process.env.EXPECTED_DIGEST, 'Python and Node must use the same payload binding');
const intent = 'a'.repeat(32);
let now = 1_800_000_000_000;
Date.now = () => now;
let auth = 0, orders = 0, captured = null, mode = 'delayed_auth';
globalThis.fetch = async (url, options) => {
  if (String(url).includes('/oauth2/token')) {
    auth++;
    if (mode === 'delayed_auth') now += 2000;
    if (mode === 'stop_during_auth') writeFileSync(process.env.NHPLUG_PAPER_ETF_STOP_PATH, 'stop');
    return Response.json({access_token:'fake', expires_in:86400});
  }
  assert(String(url).startsWith('https://moapi.nhplug.com:8443/'));
  orders++;
  captured = JSON.parse(options.body);
  if (mode === 'transport_error') throw new Error('simulated disconnect');
  return Response.json({rsp_cd:'00048', Output_0:{mkt_orr_no:123}});
};
const stopPath = process.env.NHPLUG_PAPER_ETF_STOP_PATH;
mkdirSync(dirname(stopPath), {recursive:true});
const args = deadline => ({operationId:'krstockOrderCashBuy', input,
  order_guard:{expires_at_epoch_ms:deadline, intent_id:intent, payload_sha256:digest}});
assert.equal(schemas.get('call_api_guarded').order_guard.safeParse(undefined).success, false);
assert.equal(schemas.get('call_api_guarded').order_guard.safeParse({...args(now+1000).order_guard, extra:true}).success, false);
assert.equal(schemas.get('call_api_guarded').order_guard.safeParse({...args(now+1000).order_guard, intent_id:'bad'}).success, false);
assert.equal(schemas.get('call_api_guarded').order_guard.safeParse({...args(now+1000).order_guard, expires_at_epoch_ms:0}).success, false);
for (const operationId of ['krstockOrderCashBuy', 'krstockOrderCashSell']) {
  const rejected = await handlers.get('call_api')({operationId, input});
  assert.equal(rejected.isError, true, 'ordinary cash orders must not bypass guarded server');
}
assert.equal(auth, 0);
assert.equal(orders, 0, 'ordinary cash orders must not reach the broker');

let deadline = now + 1000;
let result = await guarded(args(deadline));
assert.equal(auth, 1);
assert.equal(orders, 0, 'auth delay must not send the order');
assert.deepEqual(result.structuredContent, {_nh_order_guard:{version:1, code:'NH_SIGNAL_EXPIRED', no_send:true,
  expires_at_epoch_ms:deadline, intent_id:intent, payload_sha256:digest}});

mode = 'normal'; deadline = now + 60000;
result = await guarded(args(deadline));
assert.equal(orders, 1);
assert.deepEqual(captured, {Input_0:input}, 'local guard metadata must not reach NH');
assert.equal(result.structuredContent.Output_0.mkt_orr_no, 123);

let clockReads = 0;
const nearDeadline = now + 1;
Date.now = () => ++clockReads >= 3 ? nearDeadline : now;
result = await guarded(args(nearDeadline));
assert.equal(result.structuredContent._nh_order_guard.code, 'NH_SIGNAL_EXPIRED');
assert.equal(orders, 1, 'deadline must be checked again after stop-file inspection');
Date.now = () => now;

writeFileSync(stopPath, 'stop');
result = await guarded(args(deadline));
assert.equal(orders, 1, 'STOP must not send an order');
assert.equal(result.structuredContent._nh_order_guard.code, 'NH_STOP_REQUESTED');
assert.equal(result.structuredContent._nh_order_guard.no_send, true);
unlinkSync(stopPath);

result = await guarded({...args(deadline), input:{...input, orr_qty:2}});
assert.equal(result.isError, true, 'changed payload must be rejected');
assert.equal(orders, 1);
assert.equal(result.structuredContent, undefined);

result = await guarded({...args(deadline), operationId:'krstockOrderCancel'});
assert.equal(result.isError, true, 'unsupported operation must be rejected');
assert.equal(orders, 1);

rmSync(dirname(stopPath), {recursive:true});
result = await guarded(args(deadline));
assert.equal(result.isError, true, 'missing stop directory is uncertain');
assert.equal(result.structuredContent, undefined);
assert.equal(orders, 1);
mkdirSync(dirname(stopPath), {recursive:true});

delete process.env.NHPLUG_PAPER_ETF_STOP_PATH;
result = await guarded(args(deadline));
assert.equal(result.isError, true, 'missing trusted stop path is uncertain');
assert.equal(result.structuredContent, undefined);
assert.equal(orders, 1);
process.env.NHPLUG_PAPER_ETF_STOP_PATH = stopPath;

const coldHandlers = new Map();
registerTools({tool:(name,description,schema,handler)=>coldHandlers.set(name,handler)},
  {...config, appKey:'another-test-key'});
mode = 'stop_during_auth';
result = await coldHandlers.get('call_api_guarded')(args(deadline));
assert.equal(result.structuredContent._nh_order_guard.code, 'NH_STOP_REQUESTED');
assert.equal(orders, 1, 'STOP created during authentication must block the order');
unlinkSync(stopPath);

const coldSellHandlers = new Map();
registerTools({tool:(name,description,schema,handler)=>coldSellHandlers.set(name,handler)},
  {...config, appKey:'sell-test-key'});
const sellOperation = 'krstockOrderCashSell';
const sellDigest = createHash('sha256').update(JSON.stringify({input:Object.fromEntries(Object.entries(input).sort()), operationId:sellOperation}), 'utf8').digest('hex');
const sellArgs = {operationId:sellOperation, input,
  order_guard:{expires_at_epoch_ms:deadline, intent_id:intent, payload_sha256:sellDigest}};
result = await coldSellHandlers.get('call_api_guarded')(sellArgs);
assert.equal(result.structuredContent._nh_order_guard.code, 'NH_STOP_REQUESTED');
assert.equal(orders, 1, 'STOP during protective-sell authentication must block HTTP');
unlinkSync(stopPath);

mode = 'transport_error';
result = await guarded(args(deadline));
assert.equal(orders, 2);
assert.equal(result.isError, true);
assert.equal(result.structuredContent, undefined, 'post-send failures must not claim no-send');

mode = 'normal';
const ordinary = await legacyHandlers.get('call_api')({operationId:'krstockOrderCashBuy', input});
assert.equal(ordinary.structuredContent.Output_0.mkt_orr_no, 123, 'legacy tool stays compatible');
const beforeAllowed = orders;
assert.equal((await handlers.get('call_api')({operationId:'krstockQuoteCurrentPrice', input:{iem_cd:'069500'}})).isError, undefined);
assert.equal((await handlers.get('call_api')({operationId:'krstockOrderCancel', input:{}})).isError, undefined);
assert.equal(orders, beforeAllowed + 2, 'guarded server must retain read and cancellation calls');

const liveHandlers = new Map();
registerTools({tool:(name,description,schema,handler)=>liveHandlers.set(name,handler)},
  {...config, baseUrl:'https://api.nhplug.com:8443'});
const before = orders;
assert.equal((await liveHandlers.get('call_api_guarded')(args(deadline))).isError, true);
assert.equal(orders, before, 'guarded tool must reject the live host');

const server = new McpServer({name:'deadline-test-server', version:'1.0.0'});
registerTools(server, config);
const client = new Client({name:'deadline-test-client', version:'1.0.0'});
const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
await server.connect(serverTransport);
await client.connect(clientTransport);
assert((await client.listTools()).tools.some(tool => tool.name === 'call_api_guarded'));
const sentBeforeMcp = orders;
const mcpResult = await client.callTool({name:'call_api_guarded', arguments:args(now - 1)});
assert.equal(mcpResult.isError, undefined);
assert.equal(mcpResult.structuredContent._nh_order_guard.code, 'NH_SIGNAL_EXPIRED');
assert.equal(mcpResult.structuredContent._nh_order_guard.intent_id, intent);
assert.equal(orders, sentBeforeMcp, 'MCP response must certify pre-fetch rejection');
const missingGuard = await client.callTool({name:'call_api_guarded', arguments:{operationId:'krstockOrderCashBuy', input}});
assert.equal(missingGuard.isError, true, 'MCP schema must require the guard');
assert.equal(orders, sentBeforeMcp);
await client.close();
await server.close();
console.log('guarded paper deadline checks passed');
'''


class PaperDeadlineTest(unittest.TestCase):
    @unittest.skipUnless((ROOT / 'src').is_dir(), 'NH MCP source is unavailable')
    def test_guarded_mock_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            shutil.copytree(ROOT / 'src', temp / 'src')
            shutil.copy(ROOT / 'package.json', temp / 'package.json')
            shutil.copy(ROOT / 'tsconfig.json', temp / 'tsconfig.json')
            (temp / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)
            (temp / 'specs').symlink_to(ROOT / 'specs', target_is_directory=True)
            legacy = temp / 'legacy'
            legacy.mkdir()
            shutil.copytree(ROOT / 'src', legacy / 'src')
            shutil.copy(ROOT / 'package.json', legacy / 'package.json')
            shutil.copy(ROOT / 'tsconfig.json', legacy / 'tsconfig.json')
            (legacy / 'node_modules').symlink_to(ROOT / 'node_modules', target_is_directory=True)
            (legacy / 'specs').symlink_to(ROOT / 'specs', target_is_directory=True)
            subprocess.run([str(temp / 'node_modules/.bin/tsc'), '-p', str(legacy)], cwd=temp, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run(['patch', '--batch', '--forward', '-p1', '-i', str(PATCH)], cwd=temp,
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            subprocess.run([str(temp / 'node_modules/.bin/tsc')], cwd=temp, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            binding = {'input': {'iem_cd': '069500', 'act_no': '12345678901', 'orr_qty': 1, 'orr_pr': 70000},
                       'operationId': 'krstockOrderCashBuy'}
            expected = hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=False,
                                                  separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            env = {**os.environ, 'HOME': str(temp), 'EXPECTED_DIGEST': expected,
                   'NHPLUG_PAPER_ETF_STOP_PATH': str(temp / 'nhplug/shared-etf-paper/STOP')}
            result = subprocess.run(['node', '--input-type=module', '-e', JS], cwd=temp, env=env,
                                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertIn('guarded paper deadline checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
