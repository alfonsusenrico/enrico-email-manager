#!/usr/bin/env python3
import os
import sys
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def load_psycopg():
    try:
        import psycopg  # type: ignore
    except ImportError:
        print("ERROR: psycopg is not installed.", file=sys.stderr)
        print("Install with: python3 -m pip install psycopg[binary]", file=sys.stderr)
        return None
    return psycopg


def ensure_schema_migrations(cur):
    cur.execute(
        """
        create table if not exists schema_migrations (
          version text primary key,
          applied_at timestamptz not null default now()
        );
        """
    )


def get_applied_versions(cur):
    cur.execute("select version from schema_migrations")
    return {row[0] for row in cur.fetchall()}


def get_migration_files():
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def apply_migration(conn, path):
    version = path.stem
    sql = path.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "insert into schema_migrations (version) values (%s)",
            (version,),
        )
    conn.commit()
    print(f"Applied {version}")


def main():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1

    psycopg = load_psycopg()
    if psycopg is None:
        return 1

    migration_files = get_migration_files()
    if not migration_files:
        print("No migrations found.")
        return 0

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            ensure_schema_migrations(cur)
            applied = get_applied_versions(cur)
        conn.commit()

        for path in migration_files:
            version = path.stem
            if version in applied:
                continue
            apply_migration(conn, path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
