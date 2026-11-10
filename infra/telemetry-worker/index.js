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
//   POST /v1/ping  — LEGACY. The opt-in daily ping was retired from the app
//                    on 2026-09-28 (see docs/telemetry.md); this handler
//                    stays so installs on older builds that still send it
//                    don't 404. No new fields are planned for it.
//   POST /v1/open  — anonymous open beacon, fires at most once per UTC day
//                    per running install, not gated on opt-in. Schema v1 is
//                    three fields (schema_version, version, platform); v2
//                    adds two more local booleans (first_this_week,
//                    first_this_month) computed client-side from nothing
//                    but the install's own last-beacon date — still no
//                    install_id, no identity. The Worker additionally
//                    persists coarse edge-computed geo (ISO-2 country +
//                    first-level region name only — never city, postal
//                    code, lat/long, ASN, or the raw IP) read from
//                    request.cf. Rows therefore count install-days.
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

// Open beacon body. v1 is three required fields plus one optional `dev`
// flag. v2 adds two required booleans — first_this_week, first_this_month —
// computed by the client from nothing but its own last-beacon date, so they
// carry no identity either. No install_id, no engines list, no
// last_active_date in either version. The `dev` flag lets the maintainer's
// own installs exclude themselves from the stats page counts; setting it
// doesn't reveal identity, just marks the row as "not-a-real-user".
function validateOpen(body) {
  if (!body || typeof body !== "object") return "body must be a JSON object";
  if (body.schema_version !== 1 && body.schema_version !== 2) {
    return "schema_version must be 1 or 2";
  }
  if (typeof body.version !== "string" || !SEMVER_RE.test(body.version)) {
    return "version must be semver";
  }
  if (typeof body.platform !== "string" || !ALLOWED_PLATFORMS.has(body.platform)) {
    return "platform must be a known sys.platform value";
  }
  if (body.dev !== undefined && typeof body.dev !== "boolean") {
    return "dev must be a boolean if present";
  }
  if (body.schema_version === 2) {
    if (typeof body.first_this_week !== "boolean") {
      return "first_this_week must be a boolean for schema_version 2";
    }
    if (typeof body.first_this_month !== "boolean") {
      return "first_this_month must be a boolean for schema_version 2";
    }
  }
  return null;
}

// Coarse edge-computed geo from Cloudflare's `request.cf`. Bounded and
// sanitized on both fields; anything that doesn't look like a plain code /
// name is dropped rather than stored. NEVER read city, postalCode,
// latitude, longitude, or asn off `cf` — those fields exist on the object
// but this function must not touch them.
const COUNTRY_RE = /^[A-Z]{2}$/;
const REGION_RE = /^[A-Za-z0-9 .'-]{1,64}$/;
function sanitizeGeo(cf) {
  if (!cf || typeof cf !== "object") return { country: null, region: null };
  let country = typeof cf.country === "string" ? cf.country.toUpperCase() : null;
  if (!country || !COUNTRY_RE.test(country)) country = null;
  let region = typeof cf.region === "string" ? cf.region.trim() : null;
  if (!region || !REGION_RE.test(region)) region = null;
  return { country, region };
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
  const { country, region } = sanitizeGeo(request.cf);
  const v2 = body.schema_version === 2;
  try {
    await env.DB.prepare(
      "INSERT INTO opens (received_at, version, platform, ip_hash, is_dev, first_this_week, first_this_month, country, region) " +
      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    ).bind(
      new Date().toISOString(),
      body.version,
      body.platform,
      ipHash,
      body.dev === true ? 1 : 0,
      // v1 rows store NULL — the flags didn't exist on that client, and we
      // cannot reconstruct them after the fact.
      v2 ? (body.first_this_week ? 1 : 0) : null,
      v2 ? (body.first_this_month ? 1 : 0) : null,
      country,
      region,
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

// Minimum-count suppression: any bucket with fewer than `minCount` installs
// is folded into a single "other" bucket instead of being returned by
// name, so a country or state with one or two installs can never be read
// off the public stats page as "this one person is here." `keys` lists the
// field(s) that identify a bucket (e.g. ["country"] or ["country","region"])
// — all of them are set to "other" on the folded row so a suppressed region
// can't be paired back up with a real country.
function suppressSmallBuckets(rows, keys, countKey, minCount) {
  const kept = [];
  let otherCount = 0;
  for (const r of rows) {
    const n = r[countKey] || 0;
    if (n >= minCount) kept.push(r);
    else otherCount += n;
  }
  if (otherCount > 0) {
    const other = { [countKey]: otherCount };
    for (const k of keys) other[k] = "other";
    kept.push(other);
  }
  return kept;
}

// /v1/stats scans the event tables with about 15 queries, and D1's free
// plan stops all reads for the day after 5M rows. Cache-Control alone does
// not protect it: nothing caches a Worker response on its way out, and the
// Cache API has no effect on *.workers.dev. So the last good body lives in
// KV, which also keeps answering while D1 is refusing reads.
const STATS_CACHE_KEY = "stats:v1";
const STATS_CACHE_SECONDS = 3600;

async function readStatsCache(env) {
  if (!env.STATS_CACHE) return null;
  try {
    const entry = JSON.parse(await env.STATS_CACHE.get(STATS_CACHE_KEY));
    return entry && typeof entry.body === "string" ? entry : null;
  } catch {
    return null;
  }
}

function statsCacheResponse(body) {
  return new Response(body, { status: 200, headers: STATS_HEADERS });
}

async function handleCachedStats(request, env) {
  const cached = await readStatsCache(env);
  const now = Date.now();
  if (cached && now - cached.computed_at < STATS_CACHE_SECONDS * 1000) {
    return statsCacheResponse(cached.body);
  }
  const fresh = await handleStats(request, env);
  if (fresh.ok) {
    if (env.STATS_CACHE) {
      const body = await fresh.clone().text();
      try {
        await env.STATS_CACHE.put(STATS_CACHE_KEY, JSON.stringify({ computed_at: now, body }));
      } catch {
        // A failed cache write costs one more scan, never the answer.
      }
    }
    return fresh;
  }
  if (!cached) return fresh;
  // Label old numbers so a reader can't mistake them for today's.
  return statsCacheResponse(JSON.stringify({
    ...JSON.parse(cached.body),
    stale: true,
    stale_since: new Date(cached.computed_at).toISOString(),
  }));
}

const STATS_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "public, max-age=300",
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET",
};

// Public read-only stats endpoint. Returns aggregates only — never
// per-install rows, never raw timestamps. Always called through
// handleCachedStats.
// CORS allows GET from any origin so ccc.amirfish.ai/stats can fetch.
async function handleStats(_request, env) {
  const headers = STATS_HEADERS;
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

    // Legacy opt-in version mix, retained for API compatibility.
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

    // ISO week (UTC, Monday-Sunday) boundaries via SQLite's 'weekday N'
    // modifier: 'weekday 1' advances a date forward to the next Monday, or
    // leaves it unchanged if it's already Monday. Starting from (now - 6
    // days) therefore always lands on the Monday of the ISO week containing
    // "now", for any weekday "now" falls on (verified with the sqlite3 CLI
    // for a Monday, Wednesday and Sunday "now" substitute — see
    // ../README.md). first_this_week is set once per ISO week, so counting
    // it over a *trailing 7-day* window (the old approach) double-counted
    // any install that beaconed in both the tail of last week and the start
    // of this one; matching the exact Monday boundary fixes that.
    const weekBounds = await env.DB.prepare(
      "SELECT date('now','-6 days','weekday 1') AS this_monday, " +
      "       date(date('now','-6 days','weekday 1'), '-7 days') AS prev_monday"
    ).first();
    const thisMonday = weekBounds.this_monday;
    const prevMonday = weekBounds.prev_monday;

    // Weekly/monthly active installs, derived from the beacon-v2
    // first_this_week / first_this_month flags — each is true on exactly
    // one beacon per install per period, so counting flagged rows in the
    // matching period counts installs, not install-days, without ever
    // storing an id. "prev" is the last *complete* period. v1 beacons
    // (pre-2026-09-28) left these columns NULL and are correctly excluded.
    const weeklyMonthly = await env.DB.prepare(
      "SELECT " +
      `  (SELECT COUNT(*) FROM opens WHERE first_this_week = 1 AND received_at >= ? AND ${NODEV}) AS weekly_active_installs, ` +
      "  (SELECT COUNT(*) FROM opens WHERE first_this_week = 1 AND received_at >= ?) AS weekly_active_installs_all, " +
      `  (SELECT COUNT(*) FROM opens WHERE first_this_week = 1 AND received_at >= ? AND received_at < ? AND ${NODEV}) AS weekly_active_installs_prev, ` +
      "  (SELECT COUNT(*) FROM opens WHERE first_this_week = 1 AND received_at >= ? AND received_at < ?) AS weekly_active_installs_prev_all, " +
      `  (SELECT COUNT(*) FROM opens WHERE first_this_month = 1 AND strftime('%Y-%m', received_at) = strftime('%Y-%m','now') AND ${NODEV}) AS monthly_active_installs, ` +
      "  (SELECT COUNT(*) FROM opens WHERE first_this_month = 1 AND strftime('%Y-%m', received_at) = strftime('%Y-%m','now')) AS monthly_active_installs_all, " +
      `  (SELECT COUNT(*) FROM opens WHERE first_this_month = 1 AND strftime('%Y-%m', received_at) = strftime('%Y-%m','now','start of month','-1 day') AND ${NODEV}) AS monthly_active_installs_prev, ` +
      "  (SELECT COUNT(*) FROM opens WHERE first_this_month = 1 AND strftime('%Y-%m', received_at) = strftime('%Y-%m','now','start of month','-1 day')) AS monthly_active_installs_prev_all"
    ).bind(thisMonday, thisMonday, prevMonday, thisMonday, prevMonday, thisMonday).first();

    // Coarse geography, over one complete-ish ISO week, counting
    // *installs* (first_this_week = 1 rows), not beacons — an install that
    // beacons every day from the same rare state must count once, not 7
    // times, or it clears minimum-count suppression it should never clear.
    // Use the previous complete ISO week if it has any qualifying rows (so
    // the numbers are stable and not still filling in), else fall back to
    // the current, still-in-progress week so the card isn't empty from day
    // one. Dev rows excluded from both the "which week" check and the data.
    const prevWeekCount = await env.DB.prepare(
      `SELECT COUNT(*) AS n FROM opens WHERE first_this_week = 1 AND received_at >= ? AND received_at < ? AND ${NODEV}`
    ).bind(prevMonday, thisMonday).first();
    const usePrevWeek = (prevWeekCount.n || 0) > 0;
    const geoWeekStart = usePrevWeek ? prevMonday : thisMonday;
    const geoWeekEndClause = usePrevWeek ? "AND received_at < ?" : "";
    const geoWeekEndParams = usePrevWeek ? [thisMonday] : [];

    // One version per install's first beacon of the selected ISO week.
    const opensVersionsRaw = (await env.DB.prepare(
      "SELECT version, COUNT(*) AS installs FROM opens " +
      `WHERE first_this_week = 1 AND received_at >= ? ${geoWeekEndClause} AND ${NODEV} ` +
      "GROUP BY version ORDER BY installs DESC"
    ).bind(geoWeekStart, ...geoWeekEndParams).all()).results;

    const countriesRaw = (await env.DB.prepare(
      "SELECT country, COUNT(*) AS installs FROM opens " +
      `WHERE first_this_week = 1 AND received_at >= ? ${geoWeekEndClause} AND country IS NOT NULL AND ${NODEV} ` +
      "GROUP BY country ORDER BY installs DESC LIMIT 50"
    ).bind(geoWeekStart, ...geoWeekEndParams).all()).results;
    const regionsRaw = (await env.DB.prepare(
      "SELECT country, region, COUNT(*) AS installs FROM opens " +
      `WHERE first_this_week = 1 AND received_at >= ? ${geoWeekEndClause} AND region IS NOT NULL AND ${NODEV} ` +
      "GROUP BY country, region ORDER BY installs DESC LIMIT 25"
    ).bind(geoWeekStart, ...geoWeekEndParams).all()).results;
    const usVsIntl = await env.DB.prepare(
      "SELECT " +
      "  SUM(CASE WHEN country = 'US' THEN 1 ELSE 0 END) AS us, " +
      "  SUM(CASE WHEN country IS NOT NULL AND country != 'US' THEN 1 ELSE 0 END) AS intl " +
      `FROM opens WHERE first_this_week = 1 AND received_at >= ? ${geoWeekEndClause} AND ${NODEV}`
    ).bind(geoWeekStart, ...geoWeekEndParams).first();

    const body = JSON.stringify({
      generated_at: new Date().toISOString(),
      totals: { ...totals, ...activeWindows, ...weeklyMonthly },
      opens_by_day: opensByDay,
      pings_by_day: pingsByDay,
      downloads_by_day: downloadsByDay,
      versions,
      versions_7d: versions7d,
      opens_versions_week: suppressSmallBuckets(opensVersionsRaw, ["version"], "installs", 3),
      platforms,
      geo_week: {
        week_start: geoWeekStart,
        countries: suppressSmallBuckets(countriesRaw, ["country"], "installs", 3),
        regions: suppressSmallBuckets(regionsRaw, ["country", "region"], "installs", 3),
        us: usVsIntl.us || 0,
        intl: usVsIntl.intl || 0,
      },
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
      return handleCachedStats(request, env);
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
