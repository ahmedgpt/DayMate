from __future__ import annotations

import logging
from typing import Callable

from mcp.server.fastmcp import Context

from app.errors import DayMateError
from app.mcp_server.auth import current_user_id
from app.services.container import Services

log = logging.getLogger("daymate.tools")


def run_tool(ctx: Context, fn: Callable[[int], dict]) -> dict:
    """Resolve the trusted user, run the tool body, and return {'success': ...} without leaking internals."""
    try:
        return {"success": True, **fn(current_user_id(ctx))}
    except DayMateError as e:
        return {"success": False, "error": e.code, "message": e.message}
    except Exception:  # noqa: BLE001 - never expose stack traces or SQL to the model/user
        log.exception("tool failed")
        return {"success": False, "error": "internal_error", "message": "Something went wrong; nothing was changed."}


def local_today_for(svc: Services, user_id: int):
    from app.utils.datetime_utils import local_today
    return local_today(svc.users.get(user_id)["timezone"])
