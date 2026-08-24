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
  const env = {
    DB: {
      prepare(sql) {
        return {
          first: async () => ({
            total_opens: 3,
            total_pings: 2,
            distinct_installs: 1,
            total_downloads: 7,
          }),
          all: async () => ({
            results: sql.includes("FROM downloads")
              ? [{ day: "2026-07-15", download_clicks: 4 }]
              : [],
          }),
        };
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
