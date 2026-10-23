"""Call the live Table API as this project's agent (DESIGN.md §4).

For Claude cloud sessions: authenticates to Cloudflare Access with the project's service
token and prints the JSON response. The agent role reads the Table and Kev's Plate (never
Beth's) and writes only what SERVICE_SCOPES in app/access.py lists.

    python scripts/agent_api.py GET /api/me
    python scripts/agent_api.py GET /api/health

Reads TABLE_CF_ACCESS_CLIENT_ID / TABLE_CF_ACCESS_CLIENT_SECRET from the cloud environment
(if the environment injects them as network secrets instead, leave them unset and the
headers are added for you). TABLE_URL overrides the base URL. Never prints either
credential.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("TABLE_URL", "https://dash.sangmuah.net").rstrip("/")


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    # A redirect here is Cloudflare Access bouncing to its login page (the token was not
    # accepted). Following it would print the login HTML with a 200, so stop and report it.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def call(method: str, path: str, body: str | None = None) -> tuple[int, str]:
    # urllib's default User-Agent trips Cloudflare's Browser Integrity Check (error 1010)
    # before Access ever sees the token.
    headers = {"Accept": "application/json", "User-Agent": "table-agent/1.0"}
    client_id = os.environ.get("TABLE_CF_ACCESS_CLIENT_ID")
    client_secret = os.environ.get("TABLE_CF_ACCESS_CLIENT_SECRET")
    if client_id and client_secret:
        headers["CF-Access-Client-Id"] = client_id
        headers["CF-Access-Client-Secret"] = client_secret
    data = None
    if body is not None:
        json.loads(body)  # fail here, not on the server, if it isn't JSON
        data = body.encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=data, method=method.upper(), headers=headers)
    try:
        with _opener.open(req, timeout=60) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as err:
        if 300 <= err.code < 400:
            where = err.headers.get("Location", "")
            return err.code, f"Redirected to {where.split('?')[0]}: Cloudflare Access did not accept the service token."
        return err.code, err.read().decode(errors="replace")


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4) or not argv[2].startswith("/"):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        status, text = call(argv[1], argv[2], argv[3] if len(argv) == 4 else None)
    except urllib.error.URLError as err:
        print(f"Could not reach {BASE_URL}: {err.reason}", file=sys.stderr)
        return 1
    try:
        text = json.dumps(json.loads(text), indent=2)
    except ValueError:
        pass
    print(text)
    if status >= 300:
        print(f"HTTP {status}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
