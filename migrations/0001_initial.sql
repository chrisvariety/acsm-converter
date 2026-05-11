CREATE TABLE adept_credentials (
  id TEXT PRIMARY KEY,
  device_xml BLOB NOT NULL,
  activation_xml BLOB NOT NULL,
  devicesalt BLOB NOT NULL,
  created_at INTEGER NOT NULL
);
