#!/usr/bin/env python3
import os
import subprocess
import sys
import time

import psycopg


def wait_for_db(database_url: str, timeout_seconds: int = 60) -> None:
    deadline = time.time() + timeout_seconds
    while True:
        try:
            with psycopg.connect(database_url):
                return
        except psycopg.OperationalError as exc:
            if time.time() >= deadline:
                print(
                    f"ERROR: Database not ready after {timeout_seconds}s: {exc}",
                    file=sys.stderr,
                )
                raise
            time.sleep(2)


def main() -> int:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1

    wait_for_db(database_url)
    subprocess.run([sys.executable, "scripts/apply_migrations.py"], check=True)
    os.execv(sys.executable, [sys.executable, "-m", "app.main"])


if __name__ == "__main__":
    raise SystemExit(main())
