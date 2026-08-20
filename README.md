# ACSM Converter

A web app that converts ACSM files to standard EPUB/PDF files, powered by [libgourou](https://forge.soutade.fr/soutade/libgourou). A [Cloudflare Worker](https://developers.cloudflare.com/workers/) serves the UI and proxies to a converter running on [Fly.io](https://fly.io/).

## How it works

1. User uploads an `.acsm` file through the web interface
2. The Worker forwards the file, unchanged, to the converter on Fly.io and streams the response back to the browser
3. The converter looks up cached Adobe credentials for the file's `userId` in Postgres (activating a fresh anonymous device and caching it if there's no hit), downloads the book, and removes the DRM
4. Progress and the final EPUB/PDF are streamed back as newline-delimited JSON (NDJSON) events

The converter owns the credential cache directly (it reads and writes Postgres), so the Worker never sees or handles credentials — it's a transparent proxy.

## Concurrency

Within a machine, the two expensive stages are limited separately:

| Stage | Limit | Why |
| --- | --- | --- |
| `acsmdownloader` (fulfill + download) | `MAX_CONCURRENT_DOWNLOADS`, default 4 | Network-bound and streamed straight to disk, so it costs almost no memory and spends its time waiting on the provider. Running these one at a time meant a single slow provider stalled everyone behind it. |
| `adept_remove` (DRM removal) | 1, not configurable | Buffers the whole decrypted book in RAM (250–350MB); two at once risks an OOM kill, which burns the single-use fulfillment token. It's also pure local crypto on one shared vCPU, so parallelism would buy nothing. |

Each request gets its own `.adept` credential directory inside its work dir, which is what makes running several at once safe — the tools all take a directory argument. Requests waiting for a slot get `{"type":"waiting"}` events carrying the stage and their queue position.

### Scaling out

Because DRM removal is serialized per machine, overall throughput is capped at roughly one book per `adept_remove` run — and the only way past that is more machines:

```bash
flyctl scale count 2
```

Two things make that safe rather than just parallel:

- **`[http_service.concurrency]` in `fly.toml`.** Fly's default `soft_limit` of 20 is far above real load here, so without it every conversion lands on one machine while the other stays stopped. The configured `soft_limit` matches `MAX_CONCURRENT_DOWNLOADS`.
- **`claim_cached_creds` in `server.py`.** Two machines can miss the credential cache for the same key at the same moment and each activate a device. Only one wins the row, and the loser must then *fulfill with the winner's device* — otherwise a retry reads the cached device, fulfills from a different one, and the provider answers `E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER`, permanently bricking that ACSM. The in-process lock around activation is only a same-machine optimization; this is the part that actually holds across machines.

Machines drain on shutdown (see `kill_timeout` and `DRAIN_TIMEOUT`), so scaling down or redeploying won't kill a conversion mid-fulfillment.

## Project structure

```
src/index.js       # Cloudflare Worker — serves the UI and proxies to Fly.io
scripts/server.py  # Python HTTP server running inside the container
scripts/schema.sql # Postgres schema for the credential cache
Dockerfile         # Builds libgourou tools + the HTTP server
wrangler.jsonc     # Cloudflare Worker configuration
fly.toml           # Fly.io configuration (the converter backend)
```

## Development

```bash
npm install
npm run dev
```

## Deployment

```bash
npm run deploy
```

The app is configured to serve on `www.acsm-converter.com` via a custom domain in `wrangler.jsonc`.

## Fly.io converter backend

The converter runs on Fly.io rather than Cloudflare Containers, because Cloudflare's shared egress IPs hit rate limits from content providers (e.g. Google Play's `acs4_book_bytes` endpoint).

### 1. Provision Postgres and apply the schema

The container caches Adobe credentials in Postgres. Any reachable Postgres works (Fly Postgres, Neon, Supabase, …):

```bash
flyctl postgres create            # or use an existing database
psql "$DATABASE_URL" -f scripts/schema.sql
```

Caching is optional — if `DATABASE_URL` is unset the converter still works, but it re-activates a fresh anonymous device on every request.

### 2. Deploy the converter

```bash
flyctl launch --no-deploy   # accept the existing fly.toml; pick an app name + region
flyctl secrets set AUTH_TOKEN=$(openssl rand -hex 32)
flyctl secrets set DATABASE_URL="postgres://…"   # from step 1
flyctl deploy
```

To change how many downloads run at once, set `MAX_CONCURRENT_DOWNLOADS` in the `[env]` block of `fly.toml` (default 4). Raising it costs disk — each in-flight conversion holds an encrypted and a decrypted copy of its book — and makes more parallel requests to the same provider from a single egress IP.

Note the public URL (e.g. `https://your-app.fly.dev`) and the `AUTH_TOKEN` value.

### 3. Point the Worker at the converter

The Worker calls the Fly app URL hardcoded in `src/index.js` (`acsm-converter-fly.fly.dev`) — update it if your app name differs. Store the auth token as a Worker secret, then deploy:

```bash
npx wrangler secret put FLY_AUTH_TOKEN   # paste the same value used for AUTH_TOKEN above
npm run deploy
```

## Known error codes

The app provides user-friendly guidance for common ACSM errors:

- **E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER** — the file was already opened by a different device/account
- **E_GOOGLE_DEVICE_LIMIT_REACHED** — too many devices registered (Google Play books)
- **E_ADEPT_REQUEST_EXPIRED** — the ACSM file has expired
- **E_LIC_LICENSE_SIGN_ERROR** — a temporary issue on the content provider's end

## Credits

Built on [libgourou](https://forge.soutade.fr/soutade/libgourou) by Soutade, a free implementation of Adobe's ADEPT protocol.
