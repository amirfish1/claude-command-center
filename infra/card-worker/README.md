# Card Worker

Cloudflare Worker that hosts the shareable agent-fleet card from CCC's
Throughput page. Post intents on X, LinkedIn, Bluesky and Threads cannot attach
images, so CCC uploads the card PNG here and shares the page URL instead; the
page carries the `og:` and `twitter:` tags that make the networks show the card.

## Endpoints

- `POST /c`: `multipart/form-data` with `image` (PNG) and `title` (headline
  text). Returns `201 {"id", "url", "image"}`.
- `GET /c/<id>`: HTML page with the card, `Install CCC` and `Star on GitHub`.
- `GET /c/<id>.png`: the image.

## Limits and privacy

- PNG magic bytes and IHDR checked; only 1200x630 or 1080x1080; max 1.5 MB.
- Title is capped at 100 characters, control characters stripped, HTML-escaped.
- 10 uploads per IP per hour. The counter key is a salted hash of the IP that
  expires within the hour; nothing about the uploader is stored with a card.
- Random 128-bit ids; cards expire after one year (KV `expirationTtl`).
- Browser uploads accepted only from `localhost`, `127.0.0.1` and `[::1]`.

## Deploy

R2 is not enabled on the account, so cards live in Workers KV.

```
cd infra/card-worker
npx wrangler kv namespace create CARDS   # once; put the id in wrangler.toml
npx wrangler deploy
npm test
```

A custom domain (for example `card.getccc.dev`) needs a DNS record at the
domain's registrar and a `routes` entry in `wrangler.toml`. The client base URL
is `CARD_BASE_DEFAULT` in `static/throughput.html`.
