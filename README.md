# ACSM Converter

A humble little file format converter. In goes `.acsm`, out comes `.epub` or `.pdf`. That doesn't sound like much, but it's a wee bit tricky to do correctly, and somehow we're pushing ~1000 books per day over on free hosted version available at [acsm-converter.com](https://www.acsm-converter.com).

## What's an ACSM anyway?

You likely just bought a book from your local library or online bookstore. You want to read it on your e-reader, so you click download, but you don't end up with an EPUB or PDF... you end up with an ACSM file. Great, what now? If you're particularly tech-y, there's a variety of ways to convert it to a standard EPUB/PDF file.

But if you just want to read the book, that's why we're here: just upload the ACSM file and click the "convert" button and we'll do the rest.

## The technical details

Credit where credit is due: this wouldn't be possible without [libgourou](https://forge.soutade.fr/soutade/libgourou) by Soutade, a free implementation of Adobe's ADEPT protocol. This is pretty much a thin, hosted wrapper around it.

The thing is, that ACSM file you have isn't the book, it's more of a "ticket" you can use to exchange for the actual book. When you upload one, we:

1. Activate a device: Adobe wants every book tied to a registered "device", so we register an anonymous one on your behalf and tie it to your `userId`.
2. Fulfill and download: We hand the "ticket" to the bookstore, hopefully get a license back, and download the book.
3. Convert: Finally, we turn the book into a standard EPUB/PDF that most any e-reader can open.

## Running it yourself

If you're just looking to convert your ACSM files, you've read too far. Just upload your ACSM file to [acsm-converter.com](https://www.acsm-converter.com/) and click the "convert" button.

If you are a developer and want to run this yourself, you'll need:

1. A Postgres database
2. Somewhere to host a Docker container (e.g. [Fly.io](https://fly.io/))

Set up the database first (any Postgres will do: Neon, Supabase, Fly, one on your laptop...):

```bash
psql "$DATABASE_URL" -f scripts/schema.sql
```

Then build and run the container, pointing it at the database:

```bash
docker build -t acsm-converter .
docker run --rm -p 8080:8080 -e DATABASE_URL="postgres://..." acsm-converter
```

Open http://localhost:8080 and you should be all set. If Postgres is running on your own machine, use `host.docker.internal` rather than `localhost` in the URL, since the container has its own `localhost`.

A couple of other knobs you can fiddle with via environment variables:

- `MAX_CONCURRENT_DOWNLOADS`: how many conversions run at once (default `4`)
- `WORK_DIR`: scratch space for books mid-conversion (default `/home/libgourou/work`)
