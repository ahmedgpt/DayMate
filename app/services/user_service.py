from __future__ import annotations

from app.database import repository as repo
from app.database.connection import Database
from app.errors import NotFoundError
from app.utils.datetime_utils import get_zone


class UserService:
    def __init__(self, db: Database):
        self.db = db

    def get_or_create(self, platform: str, platform_user_id: str, chat_id: str | None = None) -> dict:
        with self.db.tx() as conn:
            return repo.get_or_create_user(conn, platform, str(platform_user_id), chat_id)

    def get(self, user_id: int) -> dict:
        with self.db.tx() as conn:
            user = repo.get_user(conn, user_id)
        if not user:
            raise NotFoundError("Unknown user.", code="unknown_user")
        return user

    def set_timezone(self, user_id: int, tz_name: str) -> dict:
        get_zone(tz_name)  # validates
        with self.db.tx() as conn:
            if not repo.get_user(conn, user_id):
                raise NotFoundError("Unknown user.", code="unknown_user")
            repo.set_user_timezone(conn, user_id, tz_name)
            return repo.get_user(conn, user_id)
