"""Offline: verify the isolated NH retry patch with fake fetch only."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path.home()/'.vibe-trading/integrations/nhplug-mcp'
PATCH = Path(__file__).resolve().parent/'nhplug-no-order-retry.patch'
JS = r'''
import assert from 'node:assert/strict';
import {callRest} from './dist/client.js';
import {registerTools} from './dist/tools.js';
const config={appKey:'fake',appSecret:'fake',baseUrl:'https://example.invalid',authUrl:'https://example.invalid'};
let mode='', count=0;
globalThis.fetch=async (url)=>{
 if(String(url).includes('/oauth2/token')) return Response.json({access_token:'fake',expires_in:86400});
 count++;
 if(mode==='network') throw new Error('mock network failure');
 if(mode==='rate') return Response.json({error_code:'IGW42902'}, {status:429});
 if(mode==='invalid400') return Response.json({error_code:'IGW40043'}, {status:400});
 if(mode==='auth' || (mode==='read' && count===1)) return Response.json({error_code:'IGW40043'}, {status:401});
 return Response.json({rsp_cd:'00000'});
};
for (const scenario of [
 ['auth','/krstock/order/v1/cashBuy',true],
 ['invalid400','/krstock/order/v1/cashBuy',true],
 ['rate','/krstock/order/v1/cashBuy',true],
 ['network','/krstock/order/v1/cashBuy',true],
 ['auth','/krstock/order/v1/cashBuy',false],
 ['auth','/some/other/trading',true],
]) {
 [mode]=scenario; count=0;
 await assert.rejects(()=>callRest(config,scenario[1],{},undefined,undefined,false,scenario[2]),
   error=>{assert.equal(error.detail.retryable,false);return true;});
 assert.equal(count,1,`order ${mode} was sent more than once`);
}
mode='read';count=0;
await callRest(config,'/krstock/quote/v1/currentPrice',{},undefined,undefined,false,false);
assert.equal(count,2,'read must retain one auth retry');
mode='network';count=0;
await assert.rejects(()=>callRest(config,'/krstock/quote/v1/currentPrice',{},undefined,undefined,false,false),
 error=>{assert.equal(error.detail.retryable,true);return true;});
assert.equal(count,1);
mode='auth';count=0;
await assert.rejects(()=>callRest(config,'/unknown',{}),error=>{assert.equal(error.detail.retryable,false);return true;});
assert.equal(count,1,'unknown operations must fail closed');
const handlers=new Map();
registerTools({tool:(name,description,schema,handler)=>handlers.set(name,handler)},{...config,enableTrading:true,defaultAccount:''});
mode='auth';count=0;
const denied=await handlers.get('call_api')({operationId:'krstockOrderCashBuy',input:{}});
assert.equal(denied.isError,true);assert.equal(count,1,'registered order was sent more than once');
assert.match(denied.content[0].text,/"retryable": false/);
mode='read';count=0;
const read=await handlers.get('call_api')({operationId:'krstockQuoteCurrentPrice',input:{}});
assert.equal(read.isError,undefined);assert.equal(count,2,'registered read must retain auth retry');
console.log('mock-only retry checks passed');
'''

class GuardedProxyTests(unittest.TestCase):
 # Needs the locally installed official NH MCP (guides/nhplug-local.md); absent on CI and under a test HOME.
 @unittest.skipUnless((ROOT/'src').is_dir() and shutil.which('node'), 'official NH MCP not installed')
 def test_no_order_retry_and_read_auth_retry(self):
  with tempfile.TemporaryDirectory() as t:
   tmp=Path(t);shutil.copytree(ROOT/'src',tmp/'src');shutil.copy(ROOT/'package.json',tmp/'package.json');shutil.copy(ROOT/'tsconfig.json',tmp/'tsconfig.json')
   (tmp/'node_modules').symlink_to(ROOT/'node_modules',target_is_directory=True)
   (tmp/'specs').symlink_to(ROOT/'specs',target_is_directory=True)
   check = subprocess.run(['patch','--batch','--forward','--dry-run','-p1','-i',str(PATCH)],cwd=tmp,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   if check.returncode == 0:
    subprocess.run(['patch','-p1','-i',str(PATCH)],cwd=tmp,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   else:
    subprocess.run(['patch','--batch','--dry-run','-R','-p1','-i',str(PATCH)],cwd=tmp,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   subprocess.run([str(tmp/'node_modules/.bin/tsc')],cwd=tmp,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   env={**os.environ,'HOME':str(tmp)}
   result=subprocess.run(['node','--input-type=module','-e',JS],cwd=tmp,env=env,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
   self.assertIn('mock-only retry checks passed',result.stdout)

if __name__=='__main__':unittest.main()
