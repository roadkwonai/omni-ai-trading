// Offline: exercise the installed official MCP with a stubbed HTTP transport.
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { homedir } from "node:os";
import { join } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";

const root = join(homedir(), ".vibe-trading/integrations/nhplug-mcp");
const temporaryHome = mkdtempSync(join(tmpdir(), "nh-structured-test-"));
process.env.HOME = temporaryHome;
const { registerTools } = await import(pathToFileURL(join(root, "dist/tools.js")));
const callbacks = new Map();
registerTools({ tool: (name, _description, _schema, handler) => callbacks.set(name, handler) }, {
  appKey: "offline-test-key", appSecret: "offline-test-secret",
  baseUrl: "https://api.nhplug.com:8443", authUrl: "https://api.nhplug.com:8443",
  enableTrading: false, defaultAccount: "",
});

let requests = 0;
globalThis.fetch = async (url) => {
  requests += 1;
  if (String(url).includes("/oauth2/token?")) {
    return Response.json({ access_token: "offline-token", expires_in: 86400 });
  }
  assert.equal(url, "https://api.nhplug.com:8443/krstock/quote/v1/currentPrice");
  return Response.json({ Output_0: { stck_prpr: 123400 }, rsp_cd: "00000" });
};
try {
  const result = await callbacks.get("get_stock_price")({ stock_code: "005930" });
  assert.deepEqual(result.structuredContent, JSON.parse(result.content[0].text));
  assert.equal(result.structuredContent.Output_0.stck_prpr, 123400);
  assert.equal(requests, 2);
  for (const operationId of ["krstockOrderCashBuy", "krstockOrderCashSell", "krstockOrderCancel"]) {
    const denied = await callbacks.get("call_api")({ operationId, input: {} });
    assert.equal(denied.isError, true, operationId);
    assert.equal(requests, 2, "readonly order must be rejected before HTTP");
  }
  console.log("NH structured quote + live-order refusal verified without network");
} finally {
  rmSync(temporaryHome, {recursive: true, force: true});
}
