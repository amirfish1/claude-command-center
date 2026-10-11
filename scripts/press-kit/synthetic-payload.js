// Synthetic, deterministic share-card payload (same shape as
// /api/throughput/share). Demo numbers only; nothing here reads real usage.
'use strict';

// Deterministic PRNG (mulberry32) so the heatmap looks organic but never changes.
function rng(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// `today` (a Date) is the last day of the year of activity.
function syntheticPayload(today) {
  const rand = rng(31000);
  const end = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 12);
  const daily = [];
  const savings = [];
  for (let i = 364; i >= 0; i--) {
    const d = new Date(end.getFullYear(), end.getMonth(), end.getDate() - i);
    const day = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    const ramp = 0.25 + 0.75 * ((364 - i) / 364); // usage grows over the year
    const weekend = d.getDay() === 0 || d.getDay() === 6;
    if (rand() < (weekend ? 0.45 : 0.08) && i > 21) continue; // idle days (none in the last 3 weeks: streak)
    const tokens = Math.round((weekend ? 0.35 : 1) * ramp * (90e6 + rand() * 210e6));
    const turns = Math.round(tokens / 260000);
    daily.push({
      day,
      tokens,
      raw_context_tokens: Math.round(tokens * 0.97),
      cache_read_tokens: Math.round(tokens * 0.97 * (0.9 + rand() * 0.08)),
      turns,
      active_duration_sec: Math.round(turns * (38 + rand() * 30)),
      cost_usd: Math.round(tokens / 1e6 * (0.55 + rand() * 0.2) * 100) / 100,
      engine_tokens: { claude: Math.round(tokens * 0.74), codex: Math.round(tokens * 0.26) },
    });
    if (i < 30 && rand() < 0.7) {
      savings.push({ day, free_saved_usd: Math.round((8 + rand() * 30) * 100) / 100, free_tokens: Math.round(tokens * 0.12), free_runs: 1 + Math.floor(rand() * 4) });
    }
  }
  const saved = savings.reduce((a, r) => a + r.free_saved_usd, 0);
  return {
    ok: true, pending: false, days: 365, generated_at: end.getTime() / 1000,
    engines: ['claude', 'codex'], daily,
    savings: {
      available: true, daily: savings,
      free_saved_usd: Math.round(saved * 100) / 100,
      free_tokens: savings.reduce((a, r) => a + r.free_tokens, 0),
      free_runs: savings.reduce((a, r) => a + r.free_runs, 0),
    },
  };
}

module.exports = { syntheticPayload };
