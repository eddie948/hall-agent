from __future__ import annotations

from typing import Any

from src.domain.context import ActorContext
from src.services.errors import ServiceError


def require_scope(actor: ActorContext, resource: dict[str, Any] | None) -> dict[str, Any]:
    if not resource:
        raise ServiceError("RESOURCE_NOT_FOUND", "未找到对应资源")
    if resource.get("scope_type") != actor.scope_type or resource.get("scope_id") != actor.scope_id:
        raise ServiceError("RESOURCE_NOT_FOUND", "未找到对应资源")
    return resource


def require_table_manager(actor: ActorContext, table: dict[str, Any]) -> None:
    require_scope(actor, table)
    if actor.scope_type == "group" and table.get("created_by") != actor.actor_user_id:
        raise ServiceError("PERMISSION_DENIED", "只有记录表创建者可以修改表结构")
