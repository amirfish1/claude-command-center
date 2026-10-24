// Ch7 — On your phone.
'use strict';
const F = require('./prep.js');
module.exports = { path: '/', viewport: '390x844', lead: 1200, tail: 2500, async run(ctx) {
  await F.prep(ctx);
  await F.caption(ctx, 'The same CCC, on your phone', { hold: 3200 });
  await F.caption(ctx, 'Tap a session to read it', { hold: 600 });
  await F.clickText(ctx, 'Fix reminder emails sent in the wrong time zone'); await ctx.pause(4500);
  await F.caption(ctx, 'Answer its question from anywhere', { hold: 3500 });
}};
