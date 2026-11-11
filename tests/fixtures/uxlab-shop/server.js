#!/usr/bin/env node
'use strict';
// Deliberately imperfect local demo. The separate seed manifest is never served.
const http = require('http');
function page(route) {
  const content = route === '/help' ? '<h1>Help center</h1><p>Nothing here yet.</p>' : `
<h1>Everyday goods</h1><p>Browse our sample collection and save a favorite.</p>
<nav><a href="/help">Get help choosing</a> <a href="#details">Product details</a></nav>
<section><h2>Canvas tote</h2><p>A simple everyday bag.</p><button id="favorite">Save favorite</button><p id="saved" role="status"></p></section>
<section><h2>Check availability</h2><form method="get" action="/availability"><label>Email <input name="email" type="email"></label>
<label>City <input name="city"></label><button type="button" id="check">Check availability</button><p id="error" role="alert"></p></form></section>
<section id="details"><h2>Product details</h2><div class="clip">The tote has a long warranty. Return instructions: bring the original receipt within thirty days of delivery.</div></section>
<section><h2>Explore colors</h2><button id="colors">Show available colors</button><p id="color-result" role="status"></p></section>
<script>
document.querySelector('#favorite').onclick = () => { if (innerWidth > 600) document.querySelector('#saved').textContent = 'Favorite saved for this visit.'; };
document.querySelector('#check').onclick = () => { document.querySelector('#error').textContent = 'Invalid input.'; };
document.querySelector('#colors').onclick = () => { setTimeout(() => { document.querySelector('#color-result').textContent = 'Available in blue and green.'; }, 6500); };
</script>`;
  return `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Everyday goods</title>
<style>body{font:18px/1.5 system-ui;max-width:700px;margin:24px auto;padding:0 20px;color:#252a32;background:#fbfaf6}section{margin:36px 0;padding:20px;border:1px solid #aaa}button,input{font:inherit;padding:10px;margin:8px 0}label{display:block}a{margin-right:18px}.clip{width:230px;height:30px;overflow:hidden;white-space:nowrap}button{cursor:pointer}</style>${content}</html>`;
}
const server = http.createServer((request, response) => {
  const route = new URL(request.url, 'http://localhost').pathname;
  if (route === '/login') {
    response.writeHead(200, {'Content-Type': 'text/html'});
    response.end('<h1>Shop login</h1><form method="post" action="/session"><label>Email<input name="email"></label><label>Password<input name="password" type="password"></label><button>Log in</button></form>'); return;
  }
  if (route === '/session' && request.method === 'POST') {
    let body = '';
    request.on('data', chunk => {body += chunk;});
    request.on('end', () => {
      const data = new URLSearchParams(body);
      if (data.get('email') !== 'fixture@example.com' || data.get('password') !== 'fixture-only-password') {
        response.writeHead(401); response.end('Login failed'); return;
      }
      response.writeHead(303, {'Set-Cookie': 'fixture_session=fixture-only-cookie; HttpOnly; SameSite=Strict; Path=/', Location: '/account'});
      response.end();
    }); return;
  }
  if (route === '/account' || route === '/account/details') {
    if (!(request.headers.cookie || '').includes('fixture_session=fixture-only-cookie')) {
      response.writeHead(401); response.end('Login required'); return;
    }
    response.writeHead(200, {'Content-Type': 'text/html'});
    response.end('<h1>Account details</h1><p>Authenticated sample shop</p><a href="/help">Help</a>'); return;
  }
  if (!['/', '/help', '/availability'].includes(route)) { response.writeHead(404); response.end('Not found'); return; }
  response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'});
  response.end(page(route));
});
if (require.main === module) server.listen(0, '127.0.0.1', () => console.log(`http://127.0.0.1:${server.address().port}`));
module.exports = {server};
