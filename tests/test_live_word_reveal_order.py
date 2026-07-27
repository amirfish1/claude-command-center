"""Regression coverage for live transcript word-reveal ordering."""

import pathlib
import subprocess
import textwrap
import unittest


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestLiveWordRevealOrder(unittest.TestCase):
    def test_inline_code_keeps_document_order_during_reveal(self):
        """Code chips must not reveal before prose that precedes them."""
        node_program = textwrap.dedent(
            r"""
            const fs = require('fs');
            const puppeteer = require('./require-puppeteer.js');
            const { findChromePath } = require('./puppeteer-browser-config.js');

            (async () => {
              const source = fs.readFileSync('static/app.js', 'utf8');
              const start = source.indexOf('function _wrapReplayWordsInHtml');
              const end = source.indexOf('function _gcReplayReceiptsHtml', start);
              if (start < 0 || end < 0) throw new Error('word-reveal helper not found');
              const helper = source.slice(start, end);
              const browser = await puppeteer.launch({
                executablePath: findChromePath(),
                args: ['--no-sandbox'],
              });
              try {
                const page = await browser.newPage();
                const order = await page.evaluate((fnSource) => {
                  eval(fnSource);
                  const wrapped = _wrapReplayWordsInHtml(
                    'alpha <code>beta</code> gamma <code>delta</code> epsilon',
                    'conv-live-word'
                  );
                  const doc = new DOMParser().parseFromString(wrapped.html, 'text/html');
                  return Array.from(doc.querySelectorAll('[data-run-id]'))
                    .sort((a, b) => Number(a.dataset.runId) - Number(b.dataset.runId))
                    .map((node) => node.textContent.trim());
                }, helper);
                const expected = ['alpha', 'beta', 'gamma', 'delta', 'epsilon'];
                if (JSON.stringify(order) !== JSON.stringify(expected)) {
                  throw new Error(`reveal order ${JSON.stringify(order)}`);
                }
              } finally {
                await browser.close();
              }
            })().catch((error) => {
              console.error(error);
              process.exit(1);
            });
            """
        )
        subprocess.run(["node", "-e", node_program], cwd=PROJECT_ROOT, check=True)


if __name__ == "__main__":
    unittest.main()
