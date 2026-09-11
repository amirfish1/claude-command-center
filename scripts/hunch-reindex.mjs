#!/usr/bin/env node
// Refresh Hunch's derived symbol/edge/component graph from the COMMITTED HEAD.
//
// Why this exists: `hunch index` refuses to run while any indexed source file
// is dirty ("refusing to persist a derived graph from dirty indexed code").
// On a shared clone with several agent sessions editing in parallel the tree
// is never clean, so the CLI path can never refresh and the graph silently
// froze (observed: 13 days stale, every new module unknown to hunch_*).
// `hunch init` indexes from `HEAD` instead, which needs no clean tree, but it
// also rewrites hooks, MCP config and CLAUDE.md. This script does only the
// index step, using the same library call `init` uses.
//
// Deterministic, no LLM, no git writes. `.hunch/*.sqlite` is gitignored.
// Wired from `.git/hooks/post-commit` via scripts/hunch-reindex.sh; safe to
// run by hand: `node scripts/hunch-reindex.mjs`.
import { existsSync, realpathSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

function git(...args) {
  return execFileSync("git", args, { encoding: "utf8" }).trim();
}

function findHunchDist() {
  const candidates = [];
  if (process.env.HUNCH_HOME) candidates.push(join(process.env.HUNCH_HOME, "dist"));
  try {
    const bin = execFileSync("sh", ["-c", "command -v hunch"], { encoding: "utf8" }).trim();
    if (bin) candidates.push(join(dirname(realpathSync(bin)), ".."));
  } catch {}
  candidates.push(join(homedir(), ".local/lib/node_modules/@davesheffer/hunch/dist"));
  try {
    const g = execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim();
    candidates.push(join(g, "@davesheffer/hunch/dist"));
  } catch {}
  for (const c of candidates) {
    if (existsSync(join(c, "extractors/indexer.js"))) return c;
  }
  throw new Error(`hunch dist not found; tried: ${candidates.join(", ")}`);
}

const root = git("rev-parse", "--show-toplevel");
if (!existsSync(join(root, ".hunch"))) {
  console.log("no .hunch/ here; nothing to do");
  process.exit(0);
}
const head = git("rev-parse", "--short", "HEAD");
const dist = findHunchDist();
const load = (rel) => import(pathToFileURL(join(dist, rel)).href);
const [{ hunchPaths }, { HunchStore }, { indexRepo }] = await Promise.all([
  load("core/paths.js"),
  load("store/hunchStore.js"),
  load("extractors/indexer.js"),
]);

const t0 = Date.now();
const store = new HunchStore(hunchPaths(root));
try {
  const res = indexRepo(store, root, { source: { kind: "commit", ref: "HEAD" } });
  store.reindex();
  const ms = Date.now() - t0;
  console.log(
    `hunch graph @ ${head}: ${res.files} files, ${res.symbols} symbols, ${res.edges} edges, ` +
      `${res.components} components in ${(ms / 1000).toFixed(1)}s` +
      (res.skipped ? ` (${res.skipped} skipped)` : ""),
  );
} finally {
  store.close();
}
