/* Major or minor for an owner question. Plain rules on the card's title, no AI
   (bodies mention risks in passing): Major if it touches one of these, in this
   order; Minor otherwise. */
(function (root) {
  'use strict';
  const major = [
    ['moves real money', /(?:\$(?!0\.)\d|\b(?:charges?|overcharge|refunds?|billing|billed|subscription|pricing|price|prices|payments?|stripe|promo code|ad spend|unpaid|referring|offer)\b)/i],
    ['reaches customers', /\b(?:send|sends|sending|tell|email|emails|replies|reply to|dms?|message to)\b.{0,60}\b(?:joyce|client|clients|customer|customers|studio|studios|host|hosts|prospect|prospects|lowcountry)\b|\b(?:cold emails?|client messaging|client-email|daily send|email replies|prospect dms?)\b/i],
    ['is public', /\b(?:public|publish|landing pages?|launch video|try becky|launch)\b/i],
    ['changes the live product', /\b(?:release|live database|production|prod|live for every|live client|go live|studio pages|live|for every studio)\b/i],
    ['changes how Becky behaves', /\b(?:becky's instructions|becky's memory|agent-memory|studio-memory)\b/i],
    ['weakens a safety guard', /\b(?:privacy guard|security guard|safety guard)\b/i],
    ["can't be undone", /\b(?:cannot be undone|can't be undone|irreversible|permanently|erase)\b/i],
  ];
  function decisionWeight(row) {
    const text = String((row && row.title) || '');
    for (const [reason, rule] of major) if (rule.test(text)) return {level: 'Major', reason};
    return {level: 'Minor', reason: 'nothing here looks like real money, customers, public, live product, Becky or a permanent change'};
  }
  root.decisionWeight = decisionWeight;
  if (typeof module !== 'undefined') module.exports = decisionWeight;
})(typeof window !== 'undefined' ? window : globalThis);
