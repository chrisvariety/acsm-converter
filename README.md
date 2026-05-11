# ACSM Converter

A web app that converts ACSM files to standard EPUB/PDF files, powered by [libgourou](https://forge.soutade.fr/soutade/libgourou) and deployed on [Cloudflare Containers](https://developers.cloudflare.com/containers/).

## How it works

1. User uploads an `.acsm` file through the web interface
2. The Worker forwards the file to a container running libgourou tools
3. The container activates an anonymous Adobe device (once per instance), downloads the book, and converts it to a standard format
4. The converted EPUB or PDF is returned to the user's browser

## Project structure

```
src/index.js       # Cloudflare Worker — serves the UI and proxies to the container
scripts/server.py  # Python HTTP server running inside the container
Dockerfile         # Builds libgourou tools + the HTTP server
wrangler.jsonc     # Cloudflare Workers/Containers configuration
fly.toml           # Fly.io configuration (alternate backend, optional)
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

## Fly.io backend (alternate)

The same container can also run on Fly.io. This is useful when Cloudflare Containers' shared egress IPs hit rate limits from content providers (e.g. Google Play's `acs4_book_bytes` endpoint).

### Deploy to Fly.io

```bash
flyctl launch --no-deploy   # accept the existing fly.toml; pick an app name + region
flyctl secrets set AUTH_TOKEN=$(openssl rand -hex 32)
flyctl deploy
```

Note the public URL of the deployed app (e.g. `https://your-app.fly.dev`) and the `AUTH_TOKEN` value you set.

### Point the Worker at Fly.io

Store the same token as a Worker secret (one-time):

```bash
npx wrangler secret put FLY_AUTH_TOKEN   # paste the same value used for AUTH_TOKEN above
```

Then in `src/index.js`, swap the container call:

```js
// Cloudflare Containers (default):
const container = getContainer(env.MY_CONTAINER, "default");
const upstream = await container.fetch("http://container/convert", {
  method: "POST",
  headers: containerHeaders,
  body,
});

// Fly.io:
const upstream = await fetch("https://your-app.fly.dev/convert", {
  method: "POST",
  headers: { ...containerHeaders, Authorization: `Bearer ${env.FLY_AUTH_TOKEN}` },
  body,
});
```

Then `npm run deploy` (or `npm run dev`) to use Fly.io. Swap back to the container fetch to return to Cloudflare Containers.

## Known error codes

The app provides user-friendly guidance for common ACSM errors:

- **E_LIC_ALREADY_FULFILLED_BY_ANOTHER_USER** — the file was already opened by a different device/account
- **E_GOOGLE_DEVICE_LIMIT_REACHED** — too many devices registered (Google Play books)
- **E_ADEPT_REQUEST_EXPIRED** — the ACSM file has expired
- **E_LIC_LICENSE_SIGN_ERROR** — a temporary issue on the content provider's end

## Credits

Built on [libgourou](https://forge.soutade.fr/soutade/libgourou) by Soutade, a free implementation of Adobe's ADEPT protocol.
