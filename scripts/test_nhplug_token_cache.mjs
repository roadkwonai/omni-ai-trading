import assert from 'node:assert/strict';
import {mkdtempSync, readdirSync, readFileSync, writeFileSync, statSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
const home=mkdtempSync(join(tmpdir(),'nh-test-'));process.env.HOME=home;
const mod=await import(pathToFileURL(join(process.env.NH_TEST_ROOT,'dist/auth.js')));
const config={appKey:'test-key',appSecret:'test-secret',authUrl:'https://api.nhplug.com:8443'};
let calls=0;globalThis.fetch=async()=>Response.json({access_token:`token-${++calls}`,expires_in:86400});
try {
 assert.equal(await mod.getAccessToken(config),'token-1');mod.clearTokenCache();
 assert.equal(await mod.getAccessToken(config),'token-1');assert.equal(calls,1);
 assert.equal(await mod.getAccessToken(config,true),'token-2');assert.equal(calls,2);
 const dir=join(home,'.vibe-trading/nhplug/tokens');const file=join(dir,readdirSync(dir)[0]);
 assert.equal(statSync(dir).mode&0o777,0o700);assert.equal(statSync(file).mode&0o777,0o600);
 writeFileSync(file,'invalid');mod.clearTokenCache();assert.equal(await mod.getAccessToken(config),'token-3');
 assert.equal(JSON.parse(readFileSync(file)).token,'token-3');console.log('token cache tests passed');
} finally {rmSync(home,{recursive:true,force:true});}
