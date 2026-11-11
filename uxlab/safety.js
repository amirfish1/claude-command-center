'use strict';
// Shared by browser enforcement and model-free unit tests.
const RISK = /\b(pay(?:ment)?|purchase|buy|checkout|subscribe|delete\s*(?:my\s*)?account|send|message|invite|real\s+people|transfer|donate|charge)\b/i;
const FAKE = Object.freeze({name: 'Test User', email: 'tester@example.com', search: 'sample',
  text: 'Sample test text', password: 'ExampleTestOnly!', phone: '000-000-0000',
  address: 'Example Street', city: 'Example City', postal: 'ABCDE', invalid: 'sample'});
function allowedURL(raw, hosts) {
  try {
    const u = new URL(raw);
    return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password &&
      hosts.includes(u.hostname.replace(/^\[|\]$/g, '').toLowerCase());
  } catch { return false; }
}
function risky(value) { return RISK.test(decodeURIComponentSafe(String(value).replace(/[+_-]/g, ' '))); }
function decodeURIComponentSafe(value) { try { return decodeURIComponent(value); } catch { return value; } }
function cardlike(value) { return /(?:\d[\s-]*){12,}/.test(String(value)); }
function fakeValue(kind, field = {}) {
  if (!Object.hasOwn(FAKE, kind)) throw Error('Choose an enumerated fake value');
  const value = FAKE[kind];
  const label = `${field.type || ''} ${field.label || ''} ${field.name || ''} ${field.autocomplete || ''}`;
  if (/card|credit|debit|cc-|cvv|cvc|iban|routing|bank|social.security/i.test(label)) throw Error('Financial and identity fields are prohibited');
  if (['file', 'hidden', 'color', 'range', 'date', 'time', 'datetime-local', 'month', 'week', 'number'].includes(field.type)) throw Error('Unsupported field type');
  if (/email/i.test(label) && kind !== 'email') throw Error('Email fields require the example.com fake email');
  if (cardlike(value) || (/\S+@\S+/.test(value) && !value.endsWith('@example.com'))) throw Error('Unsafe fake value');
  return value;
}
function requestAllowed({url, method = 'GET', body = '', resource = ''}, safety) {
  if (!allowedURL(url, safety.hosts) || ['websocket', 'eventsource'].includes(resource)) return false;
  if (cardlike(url) || cardlike(body)) return false;
  const addresses = decodeURIComponentSafe(url + ' ' + body).match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g) || [];
  if (addresses.some(address => !address.toLowerCase().endsWith('@example.com'))) return false;
  if (!safety.allow_risky && (risky(new URL(url).pathname + new URL(url).search) || risky(body))) return false;
  // Deletion is risky regardless of endpoint wording. Other writes still
  // pass the action patterns and strict fake-body checks.
  if (method.toUpperCase() === 'DELETE' && !safety.allow_risky) return false;
  // Nonempty write bodies must contain only the enumerated fake data. JSON structure
  // and field names are not user data; arbitrary identifiers/tokens are rejected.
  if (body) {
    try {
      let data;
      try { data = JSON.parse(body); } catch {
        if (!body.includes('=')) return false;
        data = Object.fromEntries(new URLSearchParams(body));
      }
      const leaves = value => {
        if (value && typeof value === 'object') {
          if (!Array.isArray(value) && Object.keys(value).some(k => !/^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$/.test(k) || /card|credit|debit|cc-|cvv|cvc|iban|routing|bank/i.test(k))) throw Error('Unsafe body keys');
          return Object.values(value).flatMap(leaves);
        }
        return [String(value ?? '')];
      };
      if (!leaves(data).every(v => Object.values(FAKE).includes(v) || ['', 'true', 'false', 'on'].includes(v))) return false;
    } catch { return false; }
  }
  return true;
}
module.exports = {RISK, FAKE, allowedURL, risky, cardlike, fakeValue, requestAllowed};
