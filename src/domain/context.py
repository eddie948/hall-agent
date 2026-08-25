from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ScopeType = Literal["user", "group"]


@dataclass(frozen=True)
class Scope:
    scope_type: ScopeType
    scope_id: str

    @classmethod
    def from_message(cls, user_id: str, chat_id: str, chat_type: str) -> "Scope":
        if chat_type == "group":
            if not chat_id:
                raise ValueError("群聊消息缺少 chat_id")
            return cls("group", chat_id)
        if not user_id:
            raise ValueError("单聊消息缺少 user_id")
        return cls("user", user_id)


@dataclass(frozen=True)
class ActorContext:
    actor_user_id: str
    scope: Scope
    chat_type: str
    message_id: str = ""

    @property
    def scope_type(self) -> ScopeType:
        return self.scope.scope_type

    @property
    def scope_id(self) -> str:
        return self.scope.scope_id
