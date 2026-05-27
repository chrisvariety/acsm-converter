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
