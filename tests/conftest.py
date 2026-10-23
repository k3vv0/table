"""Runs before any test module is imported. Points the app at a throwaway database so
`pytest` never reads or writes the real data/table.db, and blanks the Access settings so a
developer's real .env can't leak into a test (environment variables beat .env values in
pydantic-settings)."""

import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="table-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"
for _name in ("ACCESS_TEAM_DOMAIN", "ACCESS_AUD", "KEV_EMAILS", "BETH_EMAILS", "HOUSE_EMAILS", "AGENT_SERVICE_TOKEN_ID",
              "CAPTURE_SERVICE_TOKEN_ID", "SWEEP_SERVICE_TOKEN_ID", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY",
              "MAIL_ACCOUNTS", "CALENDAR_FEEDS"):
    os.environ[_name] = ""
