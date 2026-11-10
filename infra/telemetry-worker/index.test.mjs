import assert from "node:assert/strict";
import test from "node:test";

import worker from "./index.js";


test("download click stores only bounded server-side values", async () => {
  const writes = [];
  const env = {
    DB: {
      prepare(sql) {
        return {
          bind(...values) {
            writes.push({ sql, values });
            return { run: async () => ({ success: true }) };
          },
        };
      },
    },
  };
  const request = new Request("https://telemetry.example/v1/download", {
    method: "POST",
    headers: {
      "CF-Connecting-IP": "203.0.113.9",
      "User-Agent": "private test agent",
      Referer: "https://private.example/path",
      Cookie: "private=value",
    },
    body: "ignored private body",
  });

  const response = await worker.fetch(request, env);

  assert.equal(response.status, 204);
  assert.equal(writes.length, 1);
  assert.match(writes[0].sql, /INSERT INTO downloads/);
  assert.equal(writes[0].values.length, 3);
  assert.match(writes[0].values[0], /^\d{4}-\d{2}-\d{2}T/);
  assert.deepEqual(writes[0].values.slice(1), ["ccc.dmg", "landing-hero"]);
  assert.doesNotMatch(JSON.stringify(writes), /203\.0\.113\.9|private/);
});


test("download click remains opaque when D1 fails", async () => {
  const env = {
    DB: {
      prepare() {
        throw new Error("D1 unavailable");
      },
    },
  };
  const request = new Request("https://telemetry.example/v1/download", {
    method: "POST",
  });

  const response = await worker.fetch(request, env);

  assert.equal(response.status, 204);
  assert.equal(await response.text(), "");
});


test("stats exposes aggregate clicks without event rows", async () => {
  const queries = [];
  const env = {
    DB: {
      prepare(sql) {
        queries.push(sql);
        const stmt = {
          bind(...values) {
            stmt._values = values;
            return stmt;
          },
          first: async () => ({
            total_opens: 3,
            total_pings: 2,
            distinct_installs: 1,
            total_downloads: 7,
            this_monday: "2026-09-28",
            prev_monday: "2026-09-21",
            n: 0,
          }),
          all: async () => ({
            results: sql.includes("FROM downloads")
              ? [{ day: "2026-07-15", download_clicks: 4 }]
              : [],
          }),
        };
        return stmt;
      },
    },
  };

  const response = await worker.fetch(
    new Request("https://telemetry.example/v1/stats"),
    env,
  );
  const payload = await response.json();

  assert.equal(response.status, 200);
  assert.equal(payload.totals.total_downloads, 7);
  assert.deepEqual(payload.downloads_by_day, [
    { day: "2026-07-15", download_clicks: 4 },
  ]);
  assert.equal(payload.downloads, undefined);
  assert.equal(queries.filter(sql => /GROUP BY day ORDER BY day DESC LIMIT 90/.test(sql)).length, 3);
});

test("an unknown engine name never rejects the ping", async () => {
  const writes = [];
  const env = {
    DB: {
      prepare(sql) {
        return {
          bind(...values) {
            writes.push({ sql, values });
            return { run: async () => ({ success: true }) };
          },
        };
      },
    },
    IP_HASH_SECRET: "test-secret",
  };
  const body = {
    schema_version: 3,
    install_id: "00000000-0000-4000-8000-000000000002",
    version: "5.23.0",
    platform: "darwin",
    // `opencode` is known now; `warpdrive` stands in for the next engine
    // CCC learns to detect before this Worker is redeployed.
    engines: "claude,opencode,warpdrive",
    last_active_date: "2026-08-12",
    sessions_today: 1,
    active_seconds_today: 30,
    total_sessions_managed: 1,
  };
  const response = await worker.fetch(
    new Request("https://telemetry.example/v1/ping", {
      method: "POST",
      headers: { "Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.9" },
      body: JSON.stringify(body),
    }),
    env,
  );

  assert.equal(response.status, 204);
  const ping = writes.find((w) => /INSERT INTO pings/.test(w.sql));
  assert.ok(ping, "ping row written");
  // Unknown name filtered out, known ones kept in client order.
  assert.equal(ping.values[4], "claude,opencode");
});


function fakeDb(writes) {
  return {
    prepare(sql) {
      return {
        bind(...values) {
          writes.push({ sql, values });
          return { run: async () => ({ success: true }) };
        },
      };
    },
  };
}

test("open beacon v1 stores NULL for the week/month flags and geo", async () => {
  const writes = [];
  const env = { DB: fakeDb(writes) };
  const body = { schema_version: 1, version: "5.24.0", platform: "darwin" };
  const request = new Request("https://telemetry.example/v1/open", {
    method: "POST",
    headers: { "CF-Connecting-IP": "203.0.113.9" },
    body: JSON.stringify(body),
  });
  request.cf = { country: "US", region: "California" };

  const response = await worker.fetch(request, env);

  assert.equal(response.status, 204);
  const open = writes.find((w) => /INSERT INTO opens/.test(w.sql));
  assert.ok(open, "open row written");
  // received_at, version, platform, ip_hash, is_dev, first_this_week,
  // first_this_month, country, region
  assert.equal(open.values[5], null);
  assert.equal(open.values[6], null);
});

test("open beacon v2 persists week/month flags and sanitized geo", async () => {
  const writes = [];
  const env = { DB: fakeDb(writes) };
  const body = {
    schema_version: 2,
    version: "5.24.0",
    platform: "darwin",
    first_this_week: true,
    first_this_month: false,
  };
  const request = new Request("https://telemetry.example/v1/open", {
    method: "POST",
    body: JSON.stringify(body),
  });
  request.cf = { country: "us", region: "California", city: "Cupertino", postalCode: "95014" };

  const response = await worker.fetch(request, env);

  assert.equal(response.status, 204);
  const open = writes.find((w) => /INSERT INTO opens/.test(w.sql));
  assert.equal(open.values[5], 1);
  assert.equal(open.values[6], 0);
  // country is upper-cased; only country + region persisted, never city/zip.
  assert.equal(open.values[7], "US");
  assert.equal(open.values[8], "California");
  assert.doesNotMatch(JSON.stringify(writes), /Cupertino|95014/);
});

test("open beacon v2 requires the week/month booleans", async () => {
  const env = { DB: fakeDb([]) };
  const request = new Request("https://telemetry.example/v1/open", {
    method: "POST",
    body: JSON.stringify({ schema_version: 2, version: "5.24.0", platform: "darwin" }),
  });

  const response = await worker.fetch(request, env);
  assert.equal(response.status, 400);
});

test("open beacon geo sanitizer drops malformed or missing cf fields", async () => {
  const writes = [];
  const env = { DB: fakeDb(writes) };
  const body = { schema_version: 1, version: "5.24.0", platform: "darwin" };
  const request = new Request("https://telemetry.example/v1/open", {
    method: "POST",
    body: JSON.stringify(body),
  });
  request.cf = { country: "United States", region: "a".repeat(200) };

  const response = await worker.fetch(request, env);

  assert.equal(response.status, 204);
  const open = writes.find((w) => /INSERT INTO opens/.test(w.sql));
  assert.equal(open.values[7], null);
  assert.equal(open.values[8], null);
});

test("open beacon with absent request.cf stores null geo instead of throwing", async () => {
  const writes = [];
  const env = { DB: fakeDb(writes) };
  const request = new Request("https://telemetry.example/v1/open", {
    method: "POST",
    body: JSON.stringify({ schema_version: 1, version: "5.24.0", platform: "darwin" }),
  });

  const response = await worker.fetch(request, env);

  assert.equal(response.status, 204);
  const open = writes.find((w) => /INSERT INTO opens/.test(w.sql));
  assert.equal(open.values[7], null);
  assert.equal(open.values[8], null);
});

test("stats exposes weekly/monthly active installs at the exact ISO-week boundary", async () => {
  const THIS_MONDAY = "2026-09-28";
  const PREV_MONDAY = "2026-09-21";
  const queries = [];
  const env = {
    DB: {
      prepare(sql) {
        queries.push(sql);
        const stmt = {
          bind(...values) {
            stmt._values = values;
            return stmt;
          },
          first: async () => {
            if (sql.includes("AS this_monday")) {
              return { this_monday: THIS_MONDAY, prev_monday: PREV_MONDAY };
            }
            if (sql.includes("AS weekly_active_installs")) {
              return {
                weekly_active_installs: 3, weekly_active_installs_all: 4,
                weekly_active_installs_prev: 6, weekly_active_installs_prev_all: 7,
                monthly_active_installs: 10, monthly_active_installs_all: 11,
                monthly_active_installs_prev: 20, monthly_active_installs_prev_all: 21,
              };
            }
            // The "does the previous complete ISO week have any data" probe.
            if (sql.includes("SELECT COUNT(*) AS n FROM opens")) {
              return { n: 9 };
            }
            if (sql.includes("SUM(CASE WHEN country = 'US'")) {
              return { us: 5, intl: 2 };
            }
            return { total_opens: 1, total_pings: 1, distinct_installs: 1, distinct_installs_all: 1, total_downloads: 1 };
          },
          all: async () => {
            if (sql.includes("FROM opens") && sql.includes("GROUP BY version")) {
              assert.deepEqual(stmt._values, [PREV_MONDAY, THIS_MONDAY]);
              return { results: [
                { version: "5.40.0", installs: 4 },
                { version: "5.34.0", installs: 3 },
                { version: "5.39.0", installs: 2 },
                { version: "5.38.0", installs: 1 },
              ] };
            }
            if (sql.includes("GROUP BY country, region")) {
              return { results: [
                { country: "US", region: "California", installs: 4 },
                { country: "US", region: "Ohio", installs: 1 },
              ] };
            }
            if (sql.includes("GROUP BY country")) {
              return { results: [
                { country: "US", installs: 5 },
                { country: "DE", installs: 2 },
                { country: "FR", installs: 1 },
              ] };
            }
            return { results: [] };
          },
        };
        return stmt;
      },
    },
  };

  const response = await worker.fetch(new Request("https://telemetry.example/v1/stats"), env);
  const payload = await response.json();

  assert.equal(response.status, 200);
  // Old trailing-7-day double-counting field names must be gone entirely.
  assert.equal(payload.totals.weekly_new_installs, undefined);
  assert.equal(payload.totals.monthly_new_installs, undefined);
  assert.equal(payload.countries_7d, undefined);
  assert.equal(payload.regions_7d, undefined);
  assert.equal(payload.us_vs_intl_7d, undefined);

  assert.equal(payload.totals.weekly_active_installs, 3);
  assert.equal(payload.totals.weekly_active_installs_all, 4);
  assert.equal(payload.totals.weekly_active_installs_prev, 6);
  assert.equal(payload.totals.weekly_active_installs_prev_all, 7);
  assert.equal(payload.totals.monthly_active_installs, 10);
  assert.equal(payload.totals.monthly_active_installs_prev, 20);

  // Bound with the exact Monday, not a trailing 7-day window.
  const weeklyQuery = queries.find(sql => sql.includes("AS weekly_active_installs"));
  assert.match(weeklyQuery, /received_at >= \?/);
  assert.doesNotMatch(weeklyQuery, /-6 days/);

  // Previous complete week had data (n=9), so geo uses it, not the
  // still-in-progress current week.
  assert.deepEqual(payload.opens_versions_week, [
    { version: "5.40.0", installs: 4 },
    { version: "5.34.0", installs: 3 },
    { version: "other", installs: 3 },
  ]);
  const versionQueries = queries.filter(sql => sql.includes("FROM opens") && sql.includes("GROUP BY version"));
  assert.equal(versionQueries.length, 1, "only one grouped query added");
  assert.match(versionQueries[0], /COUNT\(\*\) AS installs/);
  assert.match(versionQueries[0], /first_this_week = 1/);
  assert.match(versionQueries[0], /received_at < \?/);
  assert.match(versionQueries[0], /COALESCE\(is_dev, 0\) = 0/);
  assert.doesNotMatch(versionQueries[0], /LIMIT|country IS NOT NULL/);
  assert.deepEqual(payload.geo_week, {
    week_start: PREV_MONDAY,
    countries: [
      { country: "US", installs: 5 },
      { country: "other", installs: 3 },
    ],
    regions: [
      { country: "US", region: "California", installs: 4 },
      { country: "other", region: "other", installs: 1 },
    ],
    us: 5,
    intl: 2,
  });
});

test("stats falls back to the current ISO week for geo when the previous week is empty", async () => {
  const THIS_MONDAY = "2026-09-28";
  const PREV_MONDAY = "2026-09-21";
  const env = {
    DB: {
      prepare(sql) {
        const stmt = {
          bind(...values) {
            stmt._values = values;
            return stmt;
          },
          first: async () => {
            if (sql.includes("AS this_monday")) return { this_monday: THIS_MONDAY, prev_monday: PREV_MONDAY };
            if (sql.includes("AS weekly_active_installs")) {
              return {
                weekly_active_installs: 0, weekly_active_installs_all: 0,
                weekly_active_installs_prev: 0, weekly_active_installs_prev_all: 0,
                monthly_active_installs: 0, monthly_active_installs_all: 0,
                monthly_active_installs_prev: 0, monthly_active_installs_prev_all: 0,
              };
            }
            if (sql.includes("SELECT COUNT(*) AS n FROM opens")) return { n: 0 };
            if (sql.includes("SUM(CASE WHEN country = 'US'")) return { us: 0, intl: 0 };
            return { total_opens: 0, total_pings: 0, distinct_installs: 0, distinct_installs_all: 0, total_downloads: 0 };
          },
          all: async () => {
            if (sql.includes("FROM opens") && sql.includes("GROUP BY version")) {
              assert.deepEqual(stmt._values, [THIS_MONDAY]);
              assert.doesNotMatch(sql, /received_at < /);
            }
            return { results: [] };
          },
        };
        return stmt;
      },
    },
  };

  const response = await worker.fetch(new Request("https://telemetry.example/v1/stats"), env);
  const payload = await response.json();

  assert.deepEqual(payload.opens_versions_week, []);
  assert.equal(payload.geo_week.week_start, THIS_MONDAY);
  assert.deepEqual(payload.geo_week.countries, []);
  assert.deepEqual(payload.geo_week.regions, []);
});

function statsEnv({ failReads = false } = {}) {
  const store = new Map();
  const counts = { prepares: 0 };
  const stmt = {
    bind: () => stmt,
    first: async () => {
      if (failReads) throw new Error("D1_ERROR: exceeded free tier daily row read limit");
      return {};
    },
    all: async () => {
      if (failReads) throw new Error("D1_ERROR: exceeded free tier daily row read limit");
      return { results: [] };
    },
  };
  return {
    store,
    counts,
    env: {
      DB: { prepare() { counts.prepares += 1; return stmt; } },
      STATS_CACHE: {
        get: async (key) => store.get(key) ?? null,
        put: async (key, value) => { store.set(key, value); },
      },
    },
  };
}

const statsRequest = () => new Request("https://example.test/v1/stats");

test("stats scans D1 once an hour, not once per caller", async () => {
  const { env, counts } = statsEnv();
  const first = await worker.fetch(statsRequest(), env);
  assert.equal(first.status, 200);
  const scanned = counts.prepares;
  assert.ok(scanned > 0);
  const second = await worker.fetch(statsRequest(), env);
  assert.equal(second.status, 200);
  assert.equal(counts.prepares, scanned);
  assert.deepEqual(await second.json(), await first.json());
});

test("stats rescans once the cached copy is over an hour old", async () => {
  const { env, store, counts } = statsEnv();
  await worker.fetch(statsRequest(), env);
  const scanned = counts.prepares;
  const entry = JSON.parse(store.get("stats:v1"));
  entry.computed_at -= 3601 * 1000;
  store.set("stats:v1", JSON.stringify(entry));
  await worker.fetch(statsRequest(), env);
  assert.equal(counts.prepares, scanned * 2);
});

test("stats serves the last good numbers, labelled, while D1 refuses reads", async () => {
  const computedAt = Date.parse("2026-11-06T11:00:00Z");
  const { env, store } = statsEnv({ failReads: true });
  store.set("stats:v1", JSON.stringify({
    computed_at: computedAt,
    body: JSON.stringify({ totals: { weekly_active_installs: 36 } }),
  }));
  const response = await worker.fetch(statsRequest(), env);
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.totals.weekly_active_installs, 36);
  assert.equal(body.stale, true);
  assert.equal(body.stale_since, "2026-11-06T11:00:00.000Z");
});

test("stats still reports a failure when D1 refuses and nothing is cached", async () => {
  const { env } = statsEnv({ failReads: true });
  const response = await worker.fetch(statsRequest(), env);
  assert.equal(response.status, 500);
  assert.deepEqual(await response.json(), { error: "query failed" });
});

test("stats never caches a failure", async () => {
  const { env, store } = statsEnv({ failReads: true });
  await worker.fetch(statsRequest(), env);
  assert.equal(store.size, 0);
});

test("stats works with no cache bound", async () => {
  const { env } = statsEnv();
  delete env.STATS_CACHE;
  const response = await worker.fetch(statsRequest(), env);
  assert.equal(response.status, 200);
});
