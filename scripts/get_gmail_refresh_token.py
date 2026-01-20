#!/usr/bin/env python3
import argparse
import sys

from google_auth_oauthlib.flow import InstalledAppFlow


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a Gmail OAuth refresh token using a local server flow."
    )
    parser.add_argument(
        "--client-secrets",
        required=True,
        help="Path to OAuth client_secret JSON.",
    )
    parser.add_argument(
        "--scopes",
        default="https://www.googleapis.com/auth/gmail.modify",
        help="Comma-separated OAuth scopes.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local port for the OAuth redirect.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not try to open a browser automatically.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    if not scopes:
        print("ERROR: At least one scope is required.", file=sys.stderr)
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(
        args.client_secrets,
        scopes=scopes,
    )
    creds = flow.run_local_server(
        port=args.port,
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
        open_browser=not args.no_browser,
    )

    if not creds.refresh_token:
        print("ERROR: No refresh token returned.", file=sys.stderr)
        print(
            "Try revoking access at https://myaccount.google.com/permissions and re-run.",
            file=sys.stderr,
        )
        return 1

    print("Refresh token:")
    print(creds.refresh_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
