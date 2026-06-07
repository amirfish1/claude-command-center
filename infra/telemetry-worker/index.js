// Telemetry Worker — receives two anonymous endpoints from CCC's server.py.
//
// Contract lives in /docs/telemetry.md; this file is the only code path
// that touches the wire. Four rules every handler must honor:
//   1. Drop the source IP before persistence (read-only check, never logged).
//   2. Drop unknown fields silently (forward-compat with old clients).
//   3. Reject mistyped or missing-required fields with 400 (never crash).
//   4. Return 204 on success; never echo state back to the caller.
//
// Endpoints:
//   POST /v1/ping  — opt-in daily ping with install_id (schema v1 or v2).
//                    Five fields in v1, six in v2 (adds sessions_today).
//   POST /v1/open  — anonymous open beacon, fires once per server boot,
//                    not gated on opt-in. THREE FIELDS ONLY: schema_version,
//                    version, platform. No install_id, no identity.
//
// Bound resources at deploy time (see ../README.md):
//   env.DB — Cloudflare D1 database with `pings` AND `opens` tables.
//
// The Worker is intentionally tiny — adding behaviour here is a privacy
// surface change and should be reviewed alongside the public contract.

const ALLOWED_PLATFORMS = new Set([
  "aix", "cygwin", "darwin", "freebsd", "haiku", "linux",
  "netbsd", "openbsd", "sunos", "win32", "wasi", "emscripten",
]);
const ALLOWED_ENGINES = new Set(["claude", "codex", "gemini", "cursor", "antigravity"]);
const SEMVER_RE = /^\d+\.\d+\.\d+(?:[-.+][\w.-]+)?$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function validatePing(body) {
  if (!body || typeof body !== "object") return "body must be a JSON object";
  if (body.schema_version !== 1 && body.schema_version !== 2) {
    return "schema_version must be 1 or 2";
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
  const engines = body.engines === "" ? [] : body.engines.split(",");
  for (const e of engines) {
    if (!ALLOWED_ENGINES.has(e)) return `unknown engine: ${e}`;
  }
  if (typeof body.last_active_date !== "string" ||
      (body.last_active_date !== "" && !DATE_RE.test(body.last_active_date))) {
    return "last_active_date must be YYYY-MM-DD or empty";
  }
  if (body.schema_version === 2) {
    if (!Number.isInteger(body.sessions_today) || body.sessions_today < 0 || body.sessions_today > 100000) {
      return "sessions_today must be a non-negative integer under 100000";
    }
  }
  return null;
}

// Open beacon body — three fields, on purpose. No install_id, no identity,
// no engines list, no last_active_date. Just "a CCC server booted on this
// version/platform at this UTC instant." Aggregates to a per-day boot count.
function validateOpen(body) {
  if (!body || typeof body !== "object") return "body must be a JSON object";
  if (body.schema_version !== 1) return "schema_version must be 1";
  if (typeof body.version !== "string" || !SEMVER_RE.test(body.version)) {
    return "version must be semver";
  }
  if (typeof body.platform !== "string" || !ALLOWED_PLATFORMS.has(body.platform)) {
    return "platform must be a known sys.platform value";
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
      "INSERT INTO pings (received_at, install_id, version, platform, engines, last_active_date, sessions_today) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?)"
    ).bind(
      new Date().toISOString(),
      body.install_id,
      body.version,
      body.platform,
      body.engines,
      body.last_active_date || "",
      body.schema_version === 2 ? body.sessions_today : null,
    ).run();
  } catch (_) {
    return new Response("", { status: 500 });
  }
  return new Response(null, { status: 204 });
}

async function handleOpen(request, env) {
  // eslint-disable-next-line no-unused-vars
  const _droppedIp = request.headers.get("CF-Connecting-IP");
  let body;
  try {
    body = await request.json();
  } catch (_) {
    return new Response("invalid json", { status: 400 });
  }
  const err = validateOpen(body);
  if (err) return new Response(err, { status: 400 });
  try {
    await env.DB.prepare(
      "INSERT INTO opens (received_at, version, platform) VALUES (?, ?, ?)"
    ).bind(
      new Date().toISOString(),
      body.version,
      body.platform,
    ).run();
  } catch (_) {
    return new Response("", { status: 500 });
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
  try {
    const totals = await env.DB.prepare(
      "SELECT " +
      "  (SELECT COUNT(*) FROM opens) AS total_opens, " +
      "  (SELECT COUNT(*) FROM pings) AS total_pings, " +
      "  (SELECT COUNT(DISTINCT install_id) FROM pings) AS distinct_installs"
    ).first();

    const opensByDay = (await env.DB.prepare(
      "SELECT substr(received_at, 1, 10) AS day, COUNT(*) AS boots " +
      "FROM opens GROUP BY day ORDER BY day DESC LIMIT 30"
    ).all()).results;

    const pingsByDay = (await env.DB.prepare(
      "SELECT substr(received_at, 1, 10) AS day, COUNT(DISTINCT install_id) AS active_installs " +
      "FROM pings WHERE install_id NOT LIKE '00000000%' AND install_id NOT LIKE '11111111%' AND install_id NOT LIKE '22222222%' AND install_id NOT LIKE '33333333%' " +
      "GROUP BY day ORDER BY day DESC LIMIT 30"
    ).all()).results;

    const versions = (await env.DB.prepare(
      "SELECT version, COUNT(DISTINCT install_id) AS installs FROM pings " +
      "WHERE install_id NOT LIKE '00000000%' AND install_id NOT LIKE '11111111%' AND install_id NOT LIKE '22222222%' AND install_id NOT LIKE '33333333%' " +
      "GROUP BY version ORDER BY installs DESC"
    ).all()).results;

    const platforms = (await env.DB.prepare(
      "SELECT platform, COUNT(DISTINCT install_id) AS installs FROM pings " +
      "WHERE install_id NOT LIKE '00000000%' AND install_id NOT LIKE '11111111%' AND install_id NOT LIKE '22222222%' AND install_id NOT LIKE '33333333%' " +
      "GROUP BY platform ORDER BY installs DESC"
    ).all()).results;

    const sessionsToday = (await env.DB.prepare(
      "SELECT install_id, MAX(sessions_today) AS latest_sessions_today, MAX(received_at) AS last_seen " +
      "FROM pings WHERE sessions_today IS NOT NULL AND install_id NOT LIKE '00000000%' AND install_id NOT LIKE '11111111%' AND install_id NOT LIKE '22222222%' AND install_id NOT LIKE '33333333%' " +
      "GROUP BY install_id ORDER BY last_seen DESC LIMIT 50"
    ).all()).results;

    const body = JSON.stringify({
      generated_at: new Date().toISOString(),
      totals,
      opens_by_day: opensByDay,
      pings_by_day: pingsByDay,
      versions,
      platforms,
      sessions_today_per_install: sessionsToday.map(r => ({
        install_id_prefix: r.install_id.slice(0, 8),
        latest_sessions_today: r.latest_sessions_today,
        last_seen: r.last_seen,
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
    return new Response("not found", { status: 404 });
  },
};
