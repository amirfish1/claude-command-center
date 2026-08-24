const MAX_REPORT_CHARS = 48_000;
const UUID4_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SEMVER_RE = /^\d+\.\d+\.\d+(?:[-.+][\w.-]+)?$/;
const ENVELOPE_KEYS = ['ccc_version', 'report_text', 'request_id', 'schema_version'];

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  });
}

export function validateEnvelope(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) return 'body must be an object';
  if (Object.keys(body).sort().join(',') !== ENVELOPE_KEYS.join(',')) return 'invalid envelope fields';
  if (body.schema_version !== 1) return 'schema_version must be 1';
  if (typeof body.request_id !== 'string' || !UUID4_RE.test(body.request_id)) return 'request_id must be a uuidv4';
  if (typeof body.ccc_version !== 'string' || !SEMVER_RE.test(body.ccc_version)) return 'ccc_version must be semver';
  if (typeof body.report_text !== 'string' || !body.report_text.trim()) return 'report_text is required';
  if (body.report_text.length > MAX_REPORT_CHARS) return 'report_text exceeds 48000 characters';
  return null;
}

export async function handleRequest(request, env) {
  const url = new URL(request.url);
  if (request.method !== 'POST' || url.pathname !== '/v1/report') {
    return json({ ok: false, error: 'not found' }, 404);
  }
  if (Number(request.headers.get('Content-Length') || 0) > 65_536) {
    return json({ ok: false, error: 'request too large' }, 413);
  }
  let body;
  try { body = await request.json(); } catch (_) {
    return json({ ok: false, error: 'invalid json' }, 400);
  }
  const validationError = validateEnvelope(body);
  if (validationError) return json({ ok: false, error: validationError }, 400);
  const rateKey = request.headers.get('CF-Connecting-IP') || 'unknown';
  const rate = await env.REPORT_RATE_LIMITER.limit({ key: rateKey });
  if (!rate.success) return json({ ok: false, error: 'rate limit exceeded' }, 429);
  const id = env.REPORT_SUBMISSIONS.idFromName(body.request_id);
  return env.REPORT_SUBMISSIONS.get(id).fetch(new Request('https://submission.internal/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

export class ReportSubmission {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
  }

  async fetch(request) {
    const previous = await this.ctx.storage.get('result');
    if (previous) return json(previous);
    const body = await request.json();
    const error = validateEnvelope(body);
    if (error) return json({ ok: false, error }, 400);
    const reportId = 'RPT-' + body.request_id.slice(0, 8).toUpperCase();
    try {
      await this.env.EMAIL.send({
        to: this.env.SUPPORT_TO,
        from: this.env.SUPPORT_FROM,
        subject: '[' + reportId + '] CCC private queue diagnostics',
        text: body.report_text,
      });
    } catch (_) {
      return json({ ok: false, error: 'private delivery failed' }, 502);
    }
    const result = { ok: true, report_id: reportId };
    await this.ctx.storage.put('result', result);
    await this.ctx.storage.setAlarm(Date.now() + 7 * 24 * 60 * 60 * 1000);
    return json(result);
  }

  async alarm() {
    await this.ctx.storage.deleteAll();
  }
}

export default { fetch: handleRequest };
