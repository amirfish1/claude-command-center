"""Optional real-browser regression for a transient phone refresh failure."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from ccc_server.oncall.demo_phone import Phone
from ccc_server.oncall.store import Outbound


def test_phone_recovers_after_failed_refresh():
    root = Path(__file__).resolve().parents[1]
    if not shutil.which('node'):
        pytest.skip('Node is absent')
    probe = subprocess.run(['node', '-e', 'require(process.argv[1])', str(root / 'require-puppeteer.js')],
                           capture_output=True)
    if probe.returncode:
        pytest.skip('Puppeteer is absent')
    phone = Phone(lambda text: None)
    url = phone.start([])
    phone.send(Outbound(text='An empty cart crashes. Reply zero or hide.'))
    script = '''
const puppeteer=require(ROOT+'/require-puppeteer.js');
const {findChromePath}=require(ROOT+'/puppeteer-browser-config.js');
(async()=>{
 let browser;
 try{
  browser=await puppeteer.launch({headless:true,executablePath:findChromePath(),args:['--no-sandbox','--no-proxy-server']});
  const page=await browser.newPage();let failed=false;
  await page.setRequestInterception(true);
  page.on('request',r=>{if(r.url().endsWith('/messages')&&!failed){failed=true;r.abort();}else r.continue();});
  await page.goto(URL);
  await page.waitForFunction(()=>document.querySelector('#choices button')?.textContent==='zero',{timeout:10000});
  await page.click('#choices button');
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='Reply delivered.',{timeout:10000});
  if(!failed)throw Error('Did not exercise a failed refresh');
 }finally{if(browser)await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace('ROOT', json.dumps(str(root))).replace('URL', json.dumps(url))
    try:
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert phone.replies.get(timeout=1) == 'zero'
    finally:
        phone.close()
