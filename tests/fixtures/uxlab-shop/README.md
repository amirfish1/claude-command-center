# Local UX lab fixture

Run `nice -n 15 node tests/fixtures/uxlab-shop/server.js`. It prints a loopback URL
on an ephemeral port. Stop with Ctrl-C. No packages, storage, external requests,
or real purchases. Pass the printed URL to `ccc uxlab run`.

`seeds.json` documents five deliberately planted problems for reviewers: mobile
favorite button, vague form errors, help dead end, clipped return instructions,
and delayed color feedback. It is not served by the HTTP app. Do not give the
manifest to the model testers; detection is part of the trial.

Suggested goal: “Explore the tote, save a favorite, check availability, find
return instructions and available colors, and seek help if needed.” Use the
same goal on phone and desktop to compare behavior.
