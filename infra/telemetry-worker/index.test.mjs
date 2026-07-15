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
