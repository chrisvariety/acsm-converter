-- Postgres schema for the converter's credential cache.
-- Apply once against the database referenced by DATABASE_URL, e.g.:
--   psql "$DATABASE_URL" -f scripts/schema.sql
--
-- Anonymous Adobe device credentials are cached per Adobe userId so the
-- container doesn't re-activate a fresh device on every request (which burns
-- device slots and trips provider device limits). The container reads and
-- writes this table directly.
CREATE TABLE IF NOT EXISTS adept_credentials (
  id             TEXT PRIMARY KEY,
  device_xml     BYTEA NOT NULL,
  activation_xml BYTEA NOT NULL,
  devicesalt     BYTEA NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Book downloads the provider refused with HTTP 429, waiting for the user's
-- browser to fetch the file and POST it to /finish?token=<id>. Holds the
-- fulfill reply (license + rights) and a copy of the exact device that
-- fulfilled it, since only that device can read the license. Rows are deleted
-- once the book is delivered, and pruned after PENDING_TTL_HOURS (server.py).
-- If this table is missing, the server falls back to the plain 429 error card.
CREATE TABLE IF NOT EXISTS pending_fulfillments (
  id              TEXT PRIMARY KEY,
  fulfillment_xml BYTEA NOT NULL,
  device_xml      BYTEA NOT NULL,
  activation_xml  BYTEA NOT NULL,
  devicesalt      BYTEA NOT NULL,
  download_url    TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS pending_fulfillments_created_at
  ON pending_fulfillments (created_at);

-- One row per book delivered, for the counter on the index page. Deliberately
-- nothing that identifies the book or the reader: no title, no ACSM, no IP.
-- `manual` marks books that came through the browser-assisted download
-- (/finish). If this table is missing, the page simply omits the counter.
CREATE TABLE IF NOT EXISTS conversions (
  finished_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  format      TEXT NOT NULL,     -- 'epub', 'pdf', or 'other'
  size_bytes  BIGINT NOT NULL,   -- the DRM-free file we returned
  manual      BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS conversions_finished_at
  ON conversions (finished_at);
