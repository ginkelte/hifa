-- 002_schema_migration.sql — ledger auditable de migraciones (D-006, SPEC-010 §3.5).
-- Las filas las inserta apply_migrations (§5 paso 7), no este archivo: el checksum
-- se calcula al aplicar.

CREATE TABLE schema_migration (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  checksum   TEXT NOT NULL           -- sha256 del .sql aplicado
);
