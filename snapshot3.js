const puppeteer = require('./require-puppeteer.js');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800 });
  await page.goto('http://127.0.0.1:8090', { waitUntil: 'networkidle2' });
  
  // Close the modal
  await page.evaluate(() => {
    const buttons = Array.from(document.querySelectorAll('button'));
    const letsGo = buttons.find(b => b.textContent.includes("Let's go!"));
    if (letsGo) {
      letsGo.click();
    } else {
      const close = document.querySelector('.modal-close, [aria-label="Close"], .close');
      if (close) close.click();
    }
  });
  
  await new Promise(r => setTimeout(r, 1000));
  
  // Type in the search box
  await page.evaluate(() => {
    const inputs = Array.from(document.querySelectorAll('input'));
    const search = inputs.find(i => i.placeholder && i.placeholder.includes('Search'));
    if (search) {
      search.value = '8a7c0cc6';
      search.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
  
  await new Promise(r => setTimeout(r, 1000));
  await page.screenshot({ path: 'snapshot3.png' });
  await browser.close();
})();
