# ACSM Converter

A web app that converts ACSM files to standard EPUB/PDF files, powered by [libgourou](https://forge.soutade.fr/soutade/libgourou). It runs entirely on [Fly.io](https://fly.io/): one small Python server serves the upload page and does the conversion.

## How it works

1. User uploads an `.acsm` file through the web interface
2. The converter looks up cached Adobe credentials for the file's `userId` in Postgres (activating a fresh anonymous device and caching it if there's no hit), downloads the book, and converts it to the final EPUB/PDF
3. Progress is streamed back as newline-delimited JSON (NDJSON) events, and the finished EPUB/PDF follows the terminal `result` event as raw bytes on the same connection

The converter manages the credential cache by reading from and writing to Postgres.

## Project structure

```
scripts/server.py   # Python HTTP server: serves the pages, runs conversions
scripts/templates/  # Jinja2 page templates (/, /download/<token>, expired link)
scripts/static/     # CSS and the conversion script the pages share
scripts/schema.sql  # Postgres schema for the credential cache
Dockerfile          # Builds libgourou tools + the HTTP server
fly.toml           # Fly.io configuration
```

## Development

```bash
docker build -t acsm-converter .
docker run --rm -p 8080:8080 acsm-converter
```

Then open http://localhost:8080.

## Deployment

The converter runs on Fly.io rather than Cloudflare, because Cloudflare's shared egress IPs hit rate limits from content providers (e.g. Google Play's `acs4_book_bytes` endpoint).

### 1. Provision Postgres and apply the schema

The container caches Adobe credentials in Postgres. Any reachable Postgres works (Fly Postgres, Neon, Supabase, ...):

```bash
flyctl postgres create            # or use an existing database
psql "$DATABASE_URL" -f scripts/schema.sql
```

Caching is optional. If the `DATABASE_URL` is unset, the converter still works. It re-activates a fresh anonymous device on every request.

### 2. Deploy

```bash
flyctl launch --no-deploy   # accept the existing fly.toml; pick an app name + region
flyctl secrets set DATABASE_URL="postgres://..."   # from step 1
flyctl deploy
```

To change how many downloads run at once, set `MAX_CONCURRENT_DOWNLOADS` in the `[env]` block of `fly.toml` (default 4). To handle more traffic than a single machine can process, run `flyctl scale count 2` - `fly.toml` is already configured to route requests safely across machines and to drain in-flight conversions on shutdown.

### 3. Custom domain

DNS for `acsm-converter.com` is on Cloudflare, proxied in front of Fly:

```bash
flyctl certs add www.acsm-converter.com
```

- `www`: proxied CNAME to `<app>.fly.dev`, plus the `_acme-challenge` CNAME that
  `flyctl certs` asks for (Fly can't validate through the proxy otherwise)
- SSL/TLS mode: **Full (strict)** (Flexible loops forever against `force_https`)
- Bare domain: a proxied placeholder record and a Cloudflare Redirect Rule sending it
  to `https://www.acsm-converter.com` (path and query preserved)

## Known error codes

The app provides user-friendly guidance for common ACSM errors:

- **E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER** - the file was already opened by a different device/account
- **E_GOOGLE_DEVICE_LIMIT_REACHED** - too many devices registered (Google Play books)
- **E_ADEPT_REQUEST_EXPIRED** - the ACSM file has expired
- **E_LIC_LICENSE_SIGN_ERROR** - a temporary issue on the content provider's end

## Credits

Built on [libgourou](https://forge.soutade.fr/soutade/libgourou) by Soutade, a free implementation of Adobe's ADEPT protocol.
