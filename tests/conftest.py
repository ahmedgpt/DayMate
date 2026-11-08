import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.services.container import Services  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    return Settings(database_path=str(tmp_path / "t.db"), mcp_auth_token="test-token", reminder_max_attempts=3)


@pytest.fixture
def svc(settings):
    return Services.build(settings)


@pytest.fixture
def user(svc):
    u = svc.users.get_or_create("telegram", "111", "111")
    return svc.users.set_timezone(u["id"], "Asia/Karachi")


@pytest.fixture
def other_user(svc):
    u = svc.users.get_or_create("telegram", "222", "222")
    return svc.users.set_timezone(u["id"], "Europe/London")
