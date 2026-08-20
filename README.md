# ACSM Converter

A web app that converts ACSM files to standard EPUB/PDF files, powered by [libgourou](https://forge.soutade.fr/soutade/libgourou). A [Cloudflare Worker](https://developers.cloudflare.com/workers/) serves the UI and proxies to a converter running on [Fly.io](https://fly.io/).

## How it works

1. User uploads an `.acsm` file through the web interface
2. The Worker forwards the file, unchanged, to the converter on Fly.io and streams the response back to the browser
3. The converter looks up cached Adobe credentials for the file's `userId` in Postgres (activating a fresh anonymous device and caching it if there's no hit), downloads the book, and converts it to the final EPUB/PDF
4. Progress and the final EPUB/PDF are streamed back as newline-delimited JSON (NDJSON) events

The converter manages the credential cache by reading from and writing to Postgres. This means the Worker does not see or handle credentials because it functions as a transparent proxy.

## Project structure

```
src/index.js       # Cloudflare Worker - serves the UI and proxies to Fly.io
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

The container caches Adobe credentials in Postgres. Any reachable Postgres works (Fly Postgres, Neon, Supabase, ...):

```bash
flyctl postgres create            # or use an existing database
psql "$DATABASE_URL" -f scripts/schema.sql
```

Caching is optional. If the `DATABASE_URL` is unset, the converter still works. It re-activates a fresh anonymous device on every request.

### 2. Deploy the converter

```bash
flyctl launch --no-deploy   # accept the existing fly.toml; pick an app name + region
flyctl secrets set AUTH_TOKEN=$(openssl rand -hex 32)
flyctl secrets set DATABASE_URL="postgres://..."   # from step 1
flyctl deploy
```

To change how many downloads run at once, set `MAX_CONCURRENT_DOWNLOADS` in the `[env]` block of `fly.toml` (default 4). To handle more traffic than a single machine can process, run `flyctl scale count 2` - `fly.toml` is already configured to route requests safely across machines and to drain in-flight conversions on shutdown.

Note the public URL (e.g. `https://your-app.fly.dev`) and the `AUTH_TOKEN` value.

### 3. Point the Worker at the converter

The Worker calls the Fly app URL hardcoded in `src/index.js` (`acsm-converter-fly.fly.dev`) - update it if your app name differs. Store the auth token as a Worker secret, then deploy:

```bash
npx wrangler secret put FLY_AUTH_TOKEN   # paste the same value used for AUTH_TOKEN above
npm run deploy
```

## Known error codes

The app provides user-friendly guidance for common ACSM errors:

- **E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER** - the file was already opened by a different device/account
- **E_GOOGLE_DEVICE_LIMIT_REACHED** - too many devices registered (Google Play books)
- **E_ADEPT_REQUEST_EXPIRED** - the ACSM file has expired
- **E_LIC_LICENSE_SIGN_ERROR** - a temporary issue on the content provider's end

## Credits

Built on [libgourou](https://forge.soutade.fr/soutade/libgourou) by Soutade, a free implementation of Adobe's ADEPT protocol.
