"""Лог самого агента, доступный издалека.

Логи задач оркестратор забирал с любого узла, а логи агента — ниоткуда. Всё,
что агент знает про туннели, соседей и p2p, было видно только тому, у кого есть
shell на этой машине. Машина чужая: shell у владельца, а разбирается оператор.
Со стенда — из-за этого половина расследования шла вслепую.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from looma.api.app import create_app

ADMIN = {"X-Looma-Admin-Token": "s3cret-token"}


class Settings:
    admin_token = "s3cret-token"


class Hub:
    def __init__(self, text: str = "", живой: bool = True) -> None:
        self.text = text
        self.живой = живой
        self.просили = None

    async def agent_logs(self, node_id: str, *, tail_lines: int = 200):
        from looma.orchestrator.agents import AgentError

        if not self.живой:
            raise AgentError(f"узел {node_id} сейчас не на связи")
        self.просили = (node_id, tail_lines)
        return self.text

    def node_list(self):
        return []


def client(hub):
    return TestClient(create_app(agents=hub, config=Settings()))


def test_лог_узла_отдаётся_по_имени_узла():
    """Именно по узлу, а не по задаче: неполадки случаются как раз тогда,
    когда задач нет или они уже упали."""
    hub = Hub(text="туннель к соседу не открылся")
    ответ = client(hub).get("/admin/agents/GTX/logs?tail=50", headers=ADMIN)

    assert ответ.status_code == 200
    assert ответ.json()["text"] == "туннель к соседу не открылся"
    assert hub.просили == ("GTX", 50)


def test_недоступный_узел_даёт_внятный_отказ():
    ответ = client(Hub(живой=False)).get("/admin/agents/GTX/logs", headers=ADMIN)
    assert ответ.status_code == 409
    assert "не на связи" in ответ.json()["error"]["message"]


def test_лог_узла_закрыт_от_посторонних():
    """В нём адреса, идентификаторы пиров и устройство сети."""
    assert client(Hub()).get("/admin/agents/GTX/logs").status_code == 401


# ------------------------------------------------------- кольцо на стороне узла
def test_кольцо_помнит_последние_строки():
    from looma_agent.recent import Recent

    кольцо = Recent(keep=3)
    кольцо.setFormatter(logging.Formatter("%(message)s"))
    for i in range(5):
        кольцо.emit(logging.LogRecord("x", logging.INFO, "f", 1,
                                      "строка %d", (i,), None))
    assert кольцо.tail(10) == "строка 2\nстрока 3\nстрока 4"


def test_кольцо_не_роняет_то_о_чём_пишет():
    """Сбой записи лога не имеет права уронить агента."""
    from looma_agent.recent import Recent

    кольцо = Recent()
    кольцо.setFormatter(logging.Formatter("%(message)s"))
    плохая = logging.LogRecord("x", logging.INFO, "f", 1, "%d", ("не число",), None)
    кольцо.emit(плохая)          # не должно бросить
    assert кольцо.tail(10) == ""
