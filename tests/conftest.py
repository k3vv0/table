"""Runs before any test module is imported. Points the app at a throwaway database so
`pytest` never reads or writes the real data/table.db, and blanks the Access settings so a
developer's real .env can't leak into a test (environment variables beat .env values in
pydantic-settings)."""

import os
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="table-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"
for _name in ("ACCESS_TEAM_DOMAIN", "ACCESS_AUD", "KEV_EMAILS", "BETH_EMAILS", "HOUSE_EMAILS", "AGENT_SERVICE_TOKEN_ID"):
    os.environ[_name] = ""
