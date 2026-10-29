import assert from "node:assert/strict";
import test from "node:test";

import worker, { cleanTitle, pngDimensions, renderPage } from "./index.js";

function fakeKV() {
  const store = new Map();
  return {
    store,
    async get(key, opts) {
      const v = store.get(key);
      if (v === undefined) return null;
      if (opts && opts.type === "arrayBuffer") {
        return v instanceof Uint8Array ? v.buffer.slice(v.byteOffset, v.byteOffset + v.byteLength) : v;
      }
      return typeof v === "string" ? v : String(v);
    },
    async put(key, value, opts) {
      store.set(key, value);
      store.lastOpts = { ...(store.lastOpts || {}), [key]: opts };
    },
  };
}

// Smallest byte string with a valid PNG signature and IHDR of the given size.
function fakePng(w, h, extra = 0) {
  const bytes = new Uint8Array(33 + extra);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const view = new DataView(bytes.buffer);
  view.setUint32(8, 13);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  view.setUint32(16, w);
  view.setUint32(20, h);
  return bytes;
}

function uploadRequest(png, title, headers = {}) {
  const form = new FormData();
  form.set("image", new Blob([png], { type: "image/png" }), "card.png");
  if (title !== undefined) form.set("title", title);
  return new Request("https://card.example/c", { method: "POST", body: form, headers });
}

// Request.formData() bodies are streams, so Content-Length must be computed.
async function withLength(request) {
  const body = new Uint8Array(await request.clone().arrayBuffer());
  const headers = new Headers(request.headers);
  headers.set("Content-Length", String(body.length));
  return new Request(request.url, { method: request.method, body, headers });
}

async function upload(env, png, title, headers) {
  const req = await withLength(uploadRequest(png, title, headers));
  return worker.fetch(req, env);
}

test("pngDimensions accepts only the two card sizes", () => {
  assert.deepEqual(pngDimensions(fakePng(1200, 630)), { w: 1200, h: 630 });
  assert.deepEqual(pngDimensions(fakePng(1080, 1080)), { w: 1080, h: 1080 });
  assert.equal(pngDimensions(fakePng(1200, 631)), null);
  assert.equal(pngDimensions(fakePng(10, 10)), null);
  const bad = fakePng(1200, 630);
  bad[0] = 0;
  assert.equal(pngDimensions(bad), null);
  assert.equal(pngDimensions(new Uint8Array(5)), null);
});

test("cleanTitle caps length, strips control chars and dashes", () => {
  assert.equal(cleanTitle("a" + String.fromCharCode(0x2014) + "b" + String.fromCharCode(0x2013) + "c\n\td"), "a-b-c d");
  assert.equal(cleanTitle("x".repeat(500)).length, 100);
  assert.equal(cleanTitle(42), "");
  assert.equal(cleanTitle("   "), "");
});

test("upload stores the card and serves page and image", async () => {
  const env = { CARDS: fakeKV() };
  const res = await upload(env, fakePng(1200, 630, 100), "2.28B tokens processed this week", {
    Origin: "http://127.0.0.1:8090",
    "CF-Connecting-IP": "203.0.113.9",
  });
  assert.equal(res.status, 201);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), "http://127.0.0.1:8090");
  const body = await res.json();
  assert.match(body.id, /^[A-Za-z0-9_-]{22}$/);
  assert.equal(body.url, `https://card.example/c/${body.id}`);

  const page = await worker.fetch(new Request(body.url), env);
  assert.equal(page.status, 200);
  const html = await page.text();
  assert.match(html, /<meta property="og:image" content="https:\/\/card\.example\/c\/[\w-]+\.png">/);
  assert.match(html, /<meta property="og:title" content="2\.28B tokens processed this week">/);
  assert.match(html, /<meta name="twitter:card" content="summary_large_image">/);
  assert.match(html, /<meta name="twitter:image" content="https:\/\/card\.example\/c\/[\w-]+\.png">/);
  assert.match(html, /og:image:width" content="1200"/);
  assert.match(html, /Install CCC/);
  assert.match(html, /Star on GitHub/);
  assert.doesNotMatch(html, new RegExp("[\\u2013\\u2014]|&mdash;"));

  const img = await worker.fetch(new Request(body.image), env);
  assert.equal(img.headers.get("Content-Type"), "image/png");
  assert.equal((await img.arrayBuffer()).byteLength, 133);
});

test("nothing identifying is stored with a card", async () => {
  const env = { CARDS: fakeKV() };
  await upload(env, fakePng(1080, 1080), "t", {
    "CF-Connecting-IP": "203.0.113.9",
    "User-Agent": "private agent",
    Cookie: "private=1",
    Referer: "https://private.example/",
  });
  const stored = [...env.CARDS.store.entries()]
    .filter(([k]) => !k.startsWith("rl:"))
    .map(([, v]) => (typeof v === "string" ? v : ""))
    .join("");
  assert.doesNotMatch(stored, /203\.0\.113\.9|private/);
  for (const k of env.CARDS.store.keys()) assert.doesNotMatch(k, /203\.0\.113\.9/);
  const ttl = env.CARDS.store.lastOpts;
  assert.ok(Object.entries(ttl).every(([, o]) => o && o.expirationTtl > 0));
});

test("title is escaped in the page", async () => {
  const html = renderPage("a".repeat(22), { title: '<script>alert("x")</script>', w: 1200, h: 630 }, "https://card.example");
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test("rejects non-PNG, wrong size, oversize, wrong type", async () => {
  const env = { CARDS: fakeKV() };
  assert.equal((await upload(env, new TextEncoder().encode("<html>".repeat(20)), "t")).status, 400);
  assert.equal((await upload(env, fakePng(500, 500), "t")).status, 400);
  assert.equal((await upload(env, fakePng(1200, 630, 1_600_000), "t")).status, 413);
  const bad = new Request("https://card.example/c", {
    method: "POST", body: "{}", headers: { "Content-Type": "application/json", "Content-Length": "2" },
  });
  assert.equal((await worker.fetch(bad, env)).status, 415);
  assert.equal([...env.CARDS.store.keys()].filter((k) => k.startsWith("p:")).length, 0);
});

test("browser uploads only from localhost origins", async () => {
  const env = { CARDS: fakeKV() };
  const res = await upload(env, fakePng(1200, 630), "t", { Origin: "https://evil.example" });
  assert.equal(res.status, 403);
  assert.equal(res.headers.get("Access-Control-Allow-Origin"), null);
  const ok = await upload(env, fakePng(1200, 630), "t", { Origin: "http://localhost:3000" });
  assert.equal(ok.status, 201);
  const pre = await worker.fetch(new Request("https://card.example/c", {
    method: "OPTIONS", headers: { Origin: "http://localhost:8090" },
  }), env);
  assert.equal(pre.status, 204);
});

test("per-IP rate limit", async () => {
  const env = { CARDS: fakeKV() };
  const h = { "CF-Connecting-IP": "198.51.100.7" };
  for (let i = 0; i < 10; i++) assert.equal((await upload(env, fakePng(1200, 630), "t", h)).status, 201);
  assert.equal((await upload(env, fakePng(1200, 630), "t", h)).status, 429);
  const other = await upload(env, fakePng(1200, 630), "t", { "CF-Connecting-IP": "198.51.100.8" });
  assert.equal(other.status, 201);
});

test("unknown ids and paths 404", async () => {
  const env = { CARDS: fakeKV() };
  assert.equal((await worker.fetch(new Request("https://card.example/c/" + "a".repeat(22)), env)).status, 404);
  assert.equal((await worker.fetch(new Request("https://card.example/c/short"), env)).status, 404);
  assert.equal((await worker.fetch(new Request("https://card.example/"), env)).status, 404);
  assert.equal((await worker.fetch(new Request("https://card.example/c"), env)).status, 405);
});
