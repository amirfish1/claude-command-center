// Card Worker: hosts the shareable agent-fleet card so X, LinkedIn, Bluesky,
// Threads and Discord can unfurl the user's card image. Post intents cannot
// attach images, so CCC uploads the PNG here and shares this page's URL.
//
// Endpoints:
//   POST /c         multipart/form-data: `image` (PNG) and `title` (text).
//                   Returns 201 {"id": "...", "url": "...", "image": "..."}.
//   GET  /c/<id>    HTML page with og:/twitter: tags, the card, and
//                   Install / Star buttons.
//   GET  /c/<id>.png the image.
//
// What is stored: the PNG bytes, the capped headline text, and the image
// size. No IP, user agent, cookie, referrer or account identifier is
// stored with a card. The only IP-derived value is a salted hash used as a
// short-lived rate-limit counter key (expires within the hour).
//
// Bound resources (see wrangler.toml):
//   env.CARDS: Workers KV namespace.

const MAX_BYTES = 1_500_000;          // PNG size cap
const MAX_BODY = MAX_BYTES + 20_000;  // multipart envelope headroom
const MAX_TITLE = 100;
const TTL_SECONDS = 365 * 24 * 3600;  // cards expire after one year
const RATE_LIMIT = 10;                // uploads per IP per hour
const RATE_WINDOW = 3600;
const SIZES = new Set(["1200x630", "1080x1080"]);
const ID_RE = /^\/c\/([A-Za-z0-9_-]{22})(\.png)?$/;
const ORIGIN_RE = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/;
const PNG_MAGIC = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

const SITE_URL = "https://getccc.dev";
const REPO_URL = "https://github.com/amirfish1/claude-command-center";
const DESCRIPTION =
  "Made with Claude Command Center (CCC), the open-source dashboard for AI coding agents.";

// Only CCC running on this machine may call the upload from a browser.
function corsHeaders(request) {
  const origin = request.headers.get("Origin");
  if (origin && ORIGIN_RE.test(origin)) {
    return {
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      Vary: "Origin",
    };
  }
  return { Vary: "Origin" };
}

function json(request, status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      ...corsHeaders(request),
    },
  });
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// Control chars, line/paragraph separators and bidi overrides become spaces;
// en and em dashes become a hyphen.
const CONTROL_RE = new RegExp("[\\u0000-\\u001f\\u007f-\\u009f\\u2028\\u2029\\u202a-\\u202e\\u2066-\\u2069]", "g");
const DASH_RE = new RegExp("[\\u2013\\u2014]", "g");

// Headline text: plain, single line, length-capped, no dash characters that
// CCC copy avoids. Returns "" when nothing usable is left.
export function cleanTitle(raw) {
  if (typeof raw !== "string") return "";
  return raw
    .replace(CONTROL_RE, " ")
    .replace(DASH_RE, "-")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, MAX_TITLE)
    .trim();
}

// Returns {w, h} for a PNG whose IHDR matches an allowed card size, else null.
export function pngDimensions(bytes) {
  if (bytes.length < 33) return null;
  for (let i = 0; i < PNG_MAGIC.length; i++) if (bytes[i] !== PNG_MAGIC[i]) return null;
  // First chunk must be IHDR (length 13) at offset 8.
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (view.getUint32(8) !== 13) return null;
  if (String.fromCharCode(bytes[12], bytes[13], bytes[14], bytes[15]) !== "IHDR") return null;
  const w = view.getUint32(16);
  const h = view.getUint32(20);
  return SIZES.has(`${w}x${h}`) ? { w, h } : null;
}

function newId() {
  const raw = crypto.getRandomValues(new Uint8Array(16));
  let b64 = "";
  for (const b of raw) b64 += String.fromCharCode(b);
  return btoa(b64).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function hashHex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Per-IP fixed-window counter. KV is eventually consistent, so this is a
// soft limit that stops casual abuse, not a hard quota.
async function overRateLimit(request, env) {
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const bucket = Math.floor(Date.now() / 1000 / RATE_WINDOW);
  const key = "rl:" + (await hashHex(`ccc-card|${bucket}|${ip}`));
  const count = parseInt((await env.CARDS.get(key)) || "0", 10) || 0;
  if (count >= RATE_LIMIT) return true;
  await env.CARDS.put(key, String(count + 1), { expirationTtl: RATE_WINDOW + 60 });
  return false;
}

async function handleUpload(request, env) {
  const origin = request.headers.get("Origin");
  if (origin && !ORIGIN_RE.test(origin)) return json(request, 403, { error: "origin not allowed" });

  const declared = parseInt(request.headers.get("Content-Length") || "", 10);
  if (!Number.isFinite(declared)) return json(request, 411, { error: "content-length required" });
  if (declared > MAX_BODY) return json(request, 413, { error: "too large" });

  const type = request.headers.get("Content-Type") || "";
  if (!type.toLowerCase().startsWith("multipart/form-data")) {
    return json(request, 415, { error: "expected multipart/form-data" });
  }

  if (await overRateLimit(request, env)) {
    return json(request, 429, { error: "rate limited, try again later" });
  }

  let form;
  try {
    form = await request.formData();
  } catch (e) {
    return json(request, 400, { error: "bad form" });
  }
  const file = form.get("image");
  if (!file || typeof file === "string" || typeof file.arrayBuffer !== "function") {
    return json(request, 400, { error: "image required" });
  }
  if (file.size > MAX_BYTES) return json(request, 413, { error: "too large" });
  const bytes = new Uint8Array(await file.arrayBuffer());
  const dims = pngDimensions(bytes);
  if (!dims) return json(request, 400, { error: "image must be a 1200x630 or 1080x1080 PNG" });

  const title = cleanTitle(form.get("title")) || "Agent fleet card";
  const id = newId();
  const meta = JSON.stringify({ title, w: dims.w, h: dims.h });
  await env.CARDS.put("p:" + id, bytes, { expirationTtl: TTL_SECONDS });
  await env.CARDS.put("m:" + id, meta, { expirationTtl: TTL_SECONDS });

  const base = new URL(request.url).origin;
  return json(request, 201, { id, url: `${base}/c/${id}`, image: `${base}/c/${id}.png` });
}

const PAGE_CSS = `
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:28px;padding:32px 16px;background:#0d1110;color:#e8eaf0;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Inter,sans-serif}
h1{margin:0;font-size:22px;font-weight:700;text-align:center;max-width:900px}
img{display:block;width:min(100%,900px);height:auto;border-radius:14px;border:1px solid #243029;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.row{display:flex;flex-wrap:wrap;gap:14px;justify-content:center}
a.btn{display:inline-block;padding:16px 34px;border-radius:12px;font-size:20px;font-weight:700;text-decoration:none}
a.install{background:linear-gradient(135deg,#7c8cff,#4fd1c5);color:#0d1110}
a.star{background:#161b22;color:#e8eaf0;border:1px solid #39d353}
p{margin:0;color:#7d9886;font-size:15px;text-align:center}
`;

export function renderPage(id, meta, origin) {
  const title = escapeHtml(meta.title);
  const image = `${origin}/c/${id}.png`;
  const page = `${origin}/c/${id}`;
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title>
<meta name="description" content="${escapeHtml(DESCRIPTION)}">
<meta name="robots" content="noindex">
<link rel="canonical" href="${page}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Claude Command Center">
<meta property="og:title" content="${title}">
<meta property="og:description" content="${escapeHtml(DESCRIPTION)}">
<meta property="og:url" content="${page}">
<meta property="og:image" content="${image}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="${meta.w}">
<meta property="og:image:height" content="${meta.h}">
<meta property="og:image:alt" content="${title}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="${title}">
<meta name="twitter:description" content="${escapeHtml(DESCRIPTION)}">
<meta name="twitter:image" content="${image}">
<style>${PAGE_CSS}</style>
</head><body>
<h1>${title}</h1>
<img src="${image}" width="${meta.w}" height="${meta.h}" alt="${title}">
<div class="row">
<a class="btn install" href="${SITE_URL}">Install CCC</a>
<a class="btn star" href="${REPO_URL}">&#9733; Star on GitHub</a>
</div>
<p>Free and open source. Run your own fleet of AI coding agents.</p>
</body></html>`;
}

const PAGE_HEADERS = {
  "Content-Type": "text/html; charset=utf-8",
  "Cache-Control": "public, max-age=300",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Content-Security-Policy":
    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
};

async function handleGet(request, env, match) {
  const id = match[1];
  const wantImage = !!match[2];
  if (wantImage) {
    const bytes = await env.CARDS.get("p:" + id, { type: "arrayBuffer" });
    if (!bytes) return new Response("Not found", { status: 404 });
    return new Response(bytes, {
      headers: {
        "Content-Type": "image/png",
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        "Access-Control-Allow-Origin": "*",
      },
    });
  }
  const raw = await env.CARDS.get("m:" + id);
  if (!raw) return new Response("Not found", { status: 404 });
  let meta;
  try {
    meta = JSON.parse(raw);
  } catch (e) {
    return new Response("Not found", { status: 404 });
  }
  const origin = new URL(request.url).origin;
  return new Response(renderPage(id, meta, origin), { headers: PAGE_HEADERS });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/c") {
      if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(request) });
      if (request.method !== "POST") return json(request, 405, { error: "method not allowed" });
      return handleUpload(request, env);
    }
    const match = ID_RE.exec(url.pathname);
    if (match) {
      if (request.method !== "GET" && request.method !== "HEAD") return new Response("Method not allowed", { status: 405 });
      return handleGet(request, env, match);
    }
    return new Response("Not found", { status: 404 });
  },
};
