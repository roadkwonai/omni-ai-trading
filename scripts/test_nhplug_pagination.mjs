// Offline test: the official MCP is exercised with a stub NH HTTP transport.
import assert from 'node:assert/strict';
import {mkdtempSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

const root = join(process.env.USERPROFILE || process.env.HOME, '.vibe-trading/integrations/nhplug-mcp');
const temp = mkdtempSync(join(tmpdir(), 'nh-pagination-'));
process.env.HOME = temp;
const {registerTools} = await import(pathToFileURL(join(root, 'dist/tools.js')));
const handlers = new Map();
registerTools({tool: (name, _description, _schema, handler) => handlers.set(name, handler)}, {
  appKey: 'fake-key', appSecret: 'fake-secret', baseUrl: 'https://api.nhplug.com:8443',
  authUrl: 'https://api.nhplug.com:8443', enableTrading: false, defaultAccount: '',
});
let page = 0;
const requests = [];
globalThis.fetch = async (url, options) => {
  if (String(url).includes('/oauth2/token?')) return Response.json({access_token: 'fake-token', expires_in: 86400});
  requests.push(options.headers);
  page++;
  const headers = page === 1 ? {'CTS': 'cursor-1', 'CTS_FLAG': 'Y'} : page === 2 ? {'CTS_FLAG': 'N'} : page === 5 ? {'CTS_FLAG': 'N'} : {};
  return Response.json({Output_0: page === 6 ? [{ctsz16: 'legacy-1'}] : [], rsp_cd: page === 5 ? '00165' : '00000', rsp_msg: '조회가 완료되었습니다'}, {headers});
};
try {
  const call = handlers.get('call_api');
  const first = await call({operationId: 'krstockInquiryDailyOrderExecution', input: {}, want_pagination: true});
  assert.equal(first.structuredContent._nh_pagination.cts, 'cursor-1');
  assert.equal(first.structuredContent._nh_pagination.cts_flag, 'Y');
  assert.equal(first.structuredContent._nh_pagination.complete, false);
  const second = await call({operationId: 'krstockInquiryDailyOrderExecution', input: {}, cts: 'cursor-1', cts_flag: 'Y', want_pagination: true});
  assert.equal(requests[1].cts, 'cursor-1');
  assert.equal(requests[1].cts_flag, 'Y');
  assert.equal(second.structuredContent._nh_pagination.complete, true);
  const missing = await call({operationId: 'krstockInquiryDailyOrderExecution', input: {}, want_pagination: true});
  assert.equal(missing.structuredContent._nh_pagination.complete, null);
  const legacy = await call({operationId: 'krstockInquiryDailyOrderExecution', input: {}});
  assert.equal(legacy.structuredContent._nh_pagination, undefined);
  const conflicting = await call({operationId: 'krstockInquiryDailyOrderExecution', input: {}, want_pagination: true});
  assert.equal(conflicting.structuredContent._nh_pagination.complete, false);
  const legacyCursor = await call({operationId: 'krstockInquiryDailyOrderExecution', input: {}, want_pagination: true});
  assert.equal(legacyCursor.structuredContent._nh_pagination.cts, 'legacy-1');
  assert.equal(legacyCursor.structuredContent._nh_pagination.complete, false);
  const denied = await call({operationId: 'krstockOrderCashBuy', input: {}, want_pagination: true});
  assert.equal(denied.isError, true);
  assert.equal(requests.length, 6, 'blocked order must never reach the network');
  console.log('Pagination: two pages, cursor headers, unknown completeness and legacy format verified');
} finally { rmSync(temp, {recursive: true, force: true}); }
