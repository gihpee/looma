"""Очередь ожидания аренды: «подождать, пока освободятся узлы».

Заявка — это запрос аренды, который ещё не стал группой. Живёт в памяти
оркестратора: перезапуск её теряет, и это осознанно — заявка не деньги и не
результат, клиент подаст её снова, а хранить в базе то, что через шесть часов
протухает само, значит заводить сверку ещё для одной таблицы.

Поднятые и отклонённые заявки видны ещё час: клиент, вернувшийся к вкладке,
должен узнать, чем кончилось, а не увидеть пустой список.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

WAITING = "waiting"
STARTED = "started"
FAILED = "failed"
EXPIRED = "expired"
CANCELLED = "cancelled"

#: Сколько закрытая заявка ещё видна.
LINGER_S = 3600.0


@dataclass
class Ticket:
    id: str
    account_id: int
    raw: dict
    want: int
    free_then: int
    created_at: float
    expires_at: float
    state: str = WAITING
    closed_at: Optional[float] = None
    error: str = ""
    result: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "state": self.state, "label": self.raw.get("label") or "ray",
            "size": self.want, "hours": self.raw.get("hours"),
            "free_then": self.free_then,
            "created_at": self.created_at, "expires_at": self.expires_at,
            "closed_at": self.closed_at, "error": self.error,
            "group_id": self.result.get("group_id"),
            "granted": self.result.get("granted"),
        }


class WaitingRoom:
    def __init__(self, *, hours: float = 6.0, now=time.time) -> None:
        self.ttl = max(0.0, hours) * 3600
        self.now = now
        self._tickets: Dict[str, Ticket] = {}

    # -------------------------------------------------------------- ввод
    def add(self, *, account_id: int, raw: dict, want: int, free: int) -> Ticket:
        at = self.now()
        ticket = Ticket(id=secrets.token_hex(6), account_id=account_id,
                        raw=dict(raw), want=want, free_then=free,
                        created_at=at, expires_at=at + self.ttl)
        self._tickets[ticket.id] = ticket
        return ticket

    def cancel(self, ticket_id: str, account_id: Optional[int]) -> bool:
        ticket = self._tickets.get(ticket_id)
        if ticket is None or ticket.account_id != account_id:
            return False
        if ticket.state == WAITING:
            self._close(ticket, CANCELLED)
        else:
            self._tickets.pop(ticket_id, None)
        return True

    # ------------------------------------------------------------- проход
    def due(self) -> List[Ticket]:
        """Кого пробовать: ждущие, старшие первыми. Заодно списывает
        просроченные и забывает давно закрытые."""
        at = self.now()
        for ticket in list(self._tickets.values()):
            if ticket.state == WAITING and at >= ticket.expires_at:
                self._close(ticket, EXPIRED, error="за это время узлы не освободились")
            elif ticket.closed_at is not None and at - ticket.closed_at > LINGER_S:
                self._tickets.pop(ticket.id, None)
        return sorted((t for t in self._tickets.values() if t.state == WAITING),
                      key=lambda t: t.created_at)

    def done(self, ticket_id: str, result: dict) -> None:
        ticket = self._tickets.get(ticket_id)
        if ticket is not None:
            ticket.result = dict(result)
            self._close(ticket, STARTED)

    def fail(self, ticket_id: str, error: str) -> None:
        ticket = self._tickets.get(ticket_id)
        if ticket is not None:
            self._close(ticket, FAILED, error=error)

    # -------------------------------------------------------------- чтение
    def of(self, account_id: Optional[int]) -> List[Ticket]:
        if account_id is None:
            return []
        self.due()   # чтобы просроченные показались просроченными
        return sorted((t for t in self._tickets.values() if t.account_id == account_id),
                      key=lambda t: t.created_at, reverse=True)

    def __len__(self) -> int:
        return sum(1 for t in self._tickets.values() if t.state == WAITING)

    def _close(self, ticket: Ticket, state: str, *, error: str = "") -> None:
        ticket.state = state
        ticket.closed_at = self.now()
        ticket.error = error
