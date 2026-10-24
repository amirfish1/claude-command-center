// Ch8 — Safe and cheap: the Vault.
'use strict';
const F = require('./prep.js');
module.exports = { path: '/', lead: 1200, tail: 2500, async run(ctx) {
  await F.prep(ctx);
  await F.caption(ctx, 'Agents need API keys. The Vault keeps them out of chat and .env files', { hold: 3800 });
  await F.caption(ctx, '1. Open Settings', { hold: 500 });
  await ctx.click('#settingsBtn', { duration: 1100 }); await ctx.pause(2200);
  await F.caption(ctx, '2. Vault', { hold: 500 });
  await ctx.click('#settingsRailTab-vault', { duration: 1000 }); await ctx.pause(3000);
  await F.caption(ctx, '3. Saved keys show a name and the last 4 characters, never the value', { hold: 300 });
  await F.highlight(ctx, '#vaultEntriesList'); await ctx.pause(4500); await F.unhighlight(ctx);
  await F.caption(ctx, '4. Agents use a key by name: ccc vault exec --env STRIPE_API_KEY=stripe-test', { hold: 4500 });
}};
