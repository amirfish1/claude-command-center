// Telemetry Worker — receives bounded app and landing-page events.
//
// Contract lives in /docs/telemetry.md; this file is the only code path
// that touches the wire. Four rules every handler must honor:
//   1. Never persist a raw source IP (the download handler never reads it).
//   2. Drop unknown fields silently (forward-compat with old clients).
//   3. Reject mistyped or missing-required fields with 400 (never crash).
//   4. Return 204 on success; never echo state back to the caller.
//
// Endpoints:
//   POST /v1/ping  — opt-in daily ping with install_id (schema v1 or v2).
//                    Five fields in v1, six in v2 (adds sessions_today).
//   POST /v1/open  — anonymous open beacon, fires at most once per UTC day
//                    per running install (was once per boot before
//                    2026-08-12), not gated on opt-in. THREE FIELDS ONLY:
//                    schema_version, version, platform. No install_id, no
//                    identity. Rows therefore count install-days, and the
//                    `opens` table mixes both eras before 2026-08-12.
//   POST /v1/download — empty landing-page click event. The handler receives
//                       no request object and binds three fixed/bounded values.
//   GET  /v1/stats — aggregate counts only; never returns event rows.
//
// Bound resources at deploy time (see ../README.md):
//   env.DB — Cloudflare D1 database with `pings`, `opens`, and `downloads`.
//
// The Worker is intentionally tiny — adding behaviour here is a privacy
// surface change and should be reviewed alongside the public contract.

const ALLOWED_PLATFORMS = new Set([
  "aix", "cygwin", "darwin", "freebsd", "haiku", "linux",
  "netbsd", "openbsd", "sunos", "win32", "wasi", "emscripten",
]);
// Known engine names. An engine the client detects but this list doesn't
// know about must NEVER fail the whole ping — see normalizeEngines(). The
// old behavior (400 on unknown engine) silently dropped every ping from
// installs that had `kilo` or `opencode` on PATH, which is exactly the
// forward-compat failure rule 2 exists to prevent.
const ALLOWED_ENGINES = new Set([
  "claude", "codex", "gemini", "cursor", "antigravity", "kilo", "opencode",
]);
const MAX_ENGINES = 12;

// Keep the recognized engine names, drop the rest. Bounded on both count
// and the caller's string length so an unknown future name can't grow the
// stored row. Returns the canonical comma-joined string to persist.
function normalizeEngines(raw) {
  if (raw === "") return "";
  return raw
    .split(",")
    .filter((e) => ALLOWED_ENGINES.has(e))
    .slice(0, MAX_ENGINES)
    .join(",");
}
const SEMVER_RE = /^\d+\.\d+\.\d+(?:[-.+][\w.-]+)?$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function validatePing(body) {
  if (!body || typeof body !== "object") return "body must be a JSON object";
  if (body.schema_version !== 1 && body.schema_version !== 2 && body.schema_version !== 3) {
    return "schema_version must be 1, 2, or 3";
  }
  if (typeof body.install_id !== "string" || !UUID_RE.test(body.install_id)) {
    return "install_id must be a uuidv4";
  }
  if (typeof body.version !== "string" || !SEMVER_RE.test(body.version)) {
    return "version must be semver";
  }
  if (typeof body.platform !== "string" || !ALLOWED_PLATFORMS.has(body.platform)) {
    return "platform must be a known sys.platform value";
  }
  if (typeof body.engines !== "string") return "engines must be a string";
  // Length guard only. Unknown names are dropped at write time by
  // normalizeEngines(), never rejected — a newer client must not 400.
  if (body.engines.length > 200) return "engines string too long";
  if (typeof body.last_active_date !== "string" ||
      (body.last_active_date !== "" && !DATE_RE.test(body.last_active_date))) {
    return "last_active_date must be YYYY-MM-DD or empty";
  }
  // Same maintainer marker the open beacon carries. Optional, so older
  // clients keep working unchanged.
  if (body.dev !== undefined && typeof body.dev !== "boolean") {
    return "dev must be a boolean if present";
  }
  if (body.schema_version === 2 || body.schema_version === 3) {
    if (!Number.isInteger(body.sessions_today) || body.sessions_today < 0 || body.sessions_today > 100000) {
      return "sessions_today must be a non-negative integer under 100000";
    }
  }
  if (body.schema_version === 3) {
    if (!Number.isInteger(body.active_seconds_today) || body.active_seconds_today < 0 || body.active_seconds_today > 86400) {
      return "active_seconds_today must be a non-negative integer no greater than 86400";
    }
    if (!Number.isInteger(body.total_sessions_managed) || body.total_sessions_managed < 0 || body.total_sessions_managed > 10000000) {
      return "total_sessions_managed must be a non-negative integer under 10000000";
    }
  }
  return null;
}

// Open beacon body — three required fields plus one optional `dev` flag.
// No install_id, no identity, no engines list, no last_active_date. The
// `dev` flag lets the maintainer's own installs exclude themselves from
// the stats page counts; setting it doesn't reveal identity, just marks
// the row as "not-a-real-user" for filtering.
function validateOpen(body) {
  if (!body || typeof body !== "object") return "body must be a JSON object";
  if (body.schema_version !== 1) return "schema_version must be 1";
  if (typeof body.version !== "string" || !SEMVER_RE.test(body.version)) {
    return "version must be semver";
  }
  if (typeof body.platform !== "string" || !ALLOWED_PLATFORMS.has(body.platform)) {
    return "platform must be a known sys.platform value";
  }
  if (body.dev !== undefined && typeof body.dev !== "boolean") {
    return "dev must be a boolean if present";
  }
  return null;
}

async function handlePing(request, env) {
  // Touch but do not log/store the source IP. The whole point of the
  // Worker living between client and storage is this drop.
  // eslint-disable-next-line no-unused-vars
  const _droppedIp = request.headers.get("CF-Connecting-IP");
  let body;
  try {
    body = await request.json();
  } catch (_) {
    return new Response("invalid json", { status: 400 });
  }
  const err = validatePing(body);
  if (err) return new Response(err, { status: 400 });
  try {
    await env.DB.prepare(
      "INSERT INTO pings (received_at, install_id, version, platform, engines, last_active_date, sessions_today, active_seconds_today, total_sessions_managed, is_dev) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ).bind(
      new Date().toISOString(),
      body.install_id,
      body.version,
      body.platform,
      normalizeEngines(body.engines),
      body.last_active_date || "",
      body.schema_version >= 2 ? body.sessions_today : null,
      body.schema_version === 3 ? body.active_seconds_today : null,
      body.schema_version === 3 ? body.total_sessions_managed : null,
      body.dev === true ? 1 : 0,
    ).run();
  } catch (_) {
    return new Response("", { status: 500 });
  }
  return new Response(null, { status: 204 });
}

// Daily-rotating IP hash. The raw IP is never persisted — only a
// fixed-length SHA-256 of `(utc_date || server_secret || ip)`. Same IP
// on the same UTC day produces the same hash (lets us COUNT DISTINCT
// per day for "is this 1 person restarting 18 times or 18 people").
// The salt secret is a Workers secret, not in code; the date rotates
// every UTC midnight so the hash can't be used to track across days
// even by us. See docs/telemetry.md.
async function hashIpForToday(ip, env) {
  if (!ip) return null;
  const secret = env.IP_SALT_SECRET || "";
  if (!secret) return null;  // Safer to store null than a guessable hash.
  const today = new Date().toISOString().slice(0, 10);  // YYYY-MM-DD UTC
  const enc = new TextEncoder();
  const data = enc.encode(`${today}|${secret}|${ip}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
}

async function handleOpen(request, env) {
  const ip = request.headers.get("CF-Connecting-IP");
  let body;
  try {
    body = await request.json();
  } catch (_) {
    return new Response("invalid json", { status: 400 });
  }
  const err = validateOpen(body);
  if (err) return new Response(err, { status: 400 });
  let ipHash = null;
  try {
    ipHash = await hashIpForToday(ip, env);
  } catch (_) { /* best-effort — never block the insert on hash failure */ }
  try {
    await env.DB.prepare(
      "INSERT INTO opens (received_at, version, platform, ip_hash, is_dev) VALUES (?, ?, ?, ?, ?)"
    ).bind(
      new Date().toISOString(),
      body.version,
      body.platform,
      ipHash,
      body.dev === true ? 1 : 0,
    ).run();
  } catch (_) {
    return new Response("", { status: 500 });
  }
  return new Response(null, { status: 204 });
}

async function handleDownload(env) {
  try {
    await env.DB.prepare(
      "INSERT INTO downloads (received_at, artifact, source) VALUES (?, ?, ?)"
    ).bind(
      new Date().toISOString(),
      "ccc.dmg",
      "landing-hero",
    ).run();
  } catch (_) {
    // Counting never exposes storage health or enters the download path.
  }
  return new Response(null, { status: 204 });
}

// Public read-only stats endpoint. Returns aggregates only — never
// per-install rows, never raw timestamps. Cached at the edge for 5
// minutes so the docs/stats/ page can be hammered without hitting D1.
// CORS allows GET from any origin so ccc.amirfish.ai/stats can fetch.
async function handleStats(_request, env) {
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "public, max-age=300",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET",
  };
  // Test/fixture install ids never count as installs.
  const REAL = "install_id NOT LIKE '00000000%' AND install_id NOT LIKE '11111111%' " +
    "AND install_id NOT LIKE '22222222%' AND install_id NOT LIKE '33333333%'";
  // Every "user" number is reported twice: including the maintainer's own
  // machine and excluding it. One number with an unstated answer to "does
  // this count you?" is how a stats page misleads its own author.
  const NODEV = "COALESCE(is_dev, 0) = 0";
  try {
    const totals = await env.DB.prepare(
      "SELECT " +
      "  (SELECT COUNT(*) FROM opens) AS total_opens, " +
      `  (SELECT COUNT(*) FROM pings WHERE ${REAL}) AS total_pings, ` +
      `  (SELECT COUNT(DISTINCT install_id) FROM pings WHERE ${REAL}) AS distinct_installs_all, ` +
      `  (SELECT COUNT(DISTINCT install_id) FROM pings WHERE ${REAL} AND ${NODEV}) AS distinct_installs, ` +
      "  (SELECT COUNT(*) FROM downloads) AS total_downloads"
    ).first();

    // Real distinct-install counts over a window. The page used to derive
    // "active last 30d" as the max of the daily buckets, which is the busiest
    // single day, not the number of humans — it under-reports whenever
    // installs run on different days.
    const activeWindows = await env.DB.prepare(
      "SELECT " +
      "  COUNT(DISTINCT CASE WHEN received_at >= date('now','-6 days') THEN install_id END) AS active_7d_all, " +
      "  COUNT(DISTINCT CASE WHEN received_at >= date('now','-29 days') THEN install_id END) AS active_30d_all, " +
      `  COUNT(DISTINCT CASE WHEN received_at >= date('now','-6 days') AND ${NODEV} THEN install_id END) AS active_7d, ` +
      `  COUNT(DISTINCT CASE WHEN received_at >= date('now','-29 days') AND ${NODEV} THEN install_id END) AS active_30d ` +
      `FROM pings WHERE ${REAL}`
    ).first();

    const opensByDay = (await env.DB.prepare(
      "SELECT substr(received_at, 1, 10) AS day, " +
      `  SUM(CASE WHEN ${NODEV} THEN 1 ELSE 0 END) AS boots, ` +
      `  COUNT(DISTINCT CASE WHEN ${NODEV} THEN ip_hash END) AS distinct_ips, ` +
      "  COUNT(*) AS boots_all, " +
      "  COUNT(DISTINCT ip_hash) AS distinct_ips_all " +
      "FROM opens GROUP BY day ORDER BY day DESC LIMIT 90"
    ).all()).results;

    const pingsByDay = (await env.DB.prepare(
      "SELECT substr(received_at, 1, 10) AS day, " +
      "  COUNT(DISTINCT install_id) AS active_installs_all, " +
      `  COUNT(DISTINCT CASE WHEN ${NODEV} THEN install_id END) AS active_installs ` +
      `FROM pings WHERE ${REAL} GROUP BY day ORDER BY day DESC LIMIT 90`
    ).all()).results;

    const downloadsByDay = (await env.DB.prepare(
      "SELECT substr(received_at, 1, 10) AS day, COUNT(*) AS download_clicks " +
      "FROM downloads GROUP BY day ORDER BY day DESC LIMIT 90"
    ).all()).results;

    const versions = (await env.DB.prepare(
      "SELECT version, COUNT(DISTINCT install_id) AS installs FROM pings " +
      `WHERE ${REAL} AND ${NODEV} GROUP BY version ORDER BY installs DESC`
    ).all()).results;

    // Version mix of installs active in the last 7 days. The overview view
    // needs it to say how far the fleet has rolled onto the daily-beacon
    // build, which decides how much the anonymous count can be trusted.
    const versions7d = (await env.DB.prepare(
      "SELECT version, COUNT(DISTINCT install_id) AS installs FROM pings " +
      `WHERE ${REAL} AND ${NODEV} AND received_at >= date('now','-6 days') ` +
      "GROUP BY version ORDER BY installs DESC"
    ).all()).results;

    const platforms = (await env.DB.prepare(
      "SELECT platform, COUNT(DISTINCT install_id) AS installs FROM pings " +
      `WHERE ${REAL} AND ${NODEV} GROUP BY platform ORDER BY installs DESC`
    ).all()).results;

    const sessionsToday = (await env.DB.prepare(
      "SELECT install_id, " +
      "  MAX(sessions_today) AS latest_sessions_today, " +
      "  MAX(active_seconds_today) AS latest_active_seconds_today, " +
      "  MAX(total_sessions_managed) AS latest_total_sessions_managed, " +
      "  MAX(received_at) AS last_seen, " +
      "  MAX(COALESCE(is_dev, 0)) AS is_dev " +
      `FROM pings WHERE sessions_today IS NOT NULL AND ${REAL} ` +
      "GROUP BY install_id ORDER BY last_seen DESC LIMIT 50"
    ).all()).results;

    const body = JSON.stringify({
      generated_at: new Date().toISOString(),
      totals: { ...totals, ...activeWindows },
      opens_by_day: opensByDay,
      pings_by_day: pingsByDay,
      downloads_by_day: downloadsByDay,
      versions,
      versions_7d: versions7d,
      platforms,
      sessions_today_per_install: sessionsToday.map(r => ({
        install_id_prefix: r.install_id.slice(0, 8),
        latest_sessions_today: r.latest_sessions_today,
        latest_active_seconds_today: r.latest_active_seconds_today,
        latest_total_sessions_managed: r.latest_total_sessions_managed,
        last_seen: r.last_seen,
        is_dev: r.is_dev === 1,
      })),
    });
    return new Response(body, { status: 200, headers });
  } catch (e) {
    return new Response(JSON.stringify({ error: "query failed" }), {
      status: 500, headers,
    });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/v1/stats") {
      return handleStats(request, env);
    }
    if (request.method !== "POST") {
      return new Response("method not allowed", { status: 405 });
    }
    if (url.pathname === "/v1/ping") return handlePing(request, env);
    if (url.pathname === "/v1/open") return handleOpen(request, env);
    if (url.pathname === "/v1/download") return handleDownload(env);
    return new Response("not found", { status: 404 });
  },
};
