"""Кнопки, которые действуют на сам узел: перечитать железо и перезапустить.

До них единственным рычагом для ЧУЖОЙ машины была выкатка релиза. Со стенда:
у узла пропали карты, потому что его хозяин пересобирал драйвер, — а агент
определяет железо один раз, при запуске, и держит этот ответ до конца процесса.
Вернуть карты в панель было нечем, кроме обновления всего парка ради одного
узла.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from looma.api.app import create_app

ADMIN = {"X-Looma-Admin-Token": "s3cret-token"}


class Settings:
    admin_token = "s3cret-token"


class Hub:
    def __init__(self, refusal: str = "") -> None:
        self.refusal = refusal
        self.просили: list = []

    async def node_command(self, node_id: str, action: str) -> None:
        from looma.orchestrator.agents import AgentError

        self.просили.append((node_id, action))
        if self.refusal:
            raise AgentError(self.refusal)

    def node_list(self):
        return []


def client(hub):
    return TestClient(create_app(agents=hub, config=Settings()))


@pytest.mark.parametrize("кнопка", ["rescan", "restart"])
def test_кнопка_доносит_действие_до_узла(кнопка):
    hub = Hub()
    ответ = client(hub).post(f"/admin/agents/GTX/{кнопка}", headers=ADMIN)

    assert ответ.status_code == 200
    assert hub.просили == [("GTX", кнопка)]


def test_отказ_узла_виден_оператору():
    """У обеих команд отказ содержательный — «на узле работают задачи» или
    «агент не знает такого действия». Проглотить его значит оставить человека
    с кнопкой, которая гаснет и молчит."""
    hub = Hub(refusal="на узле 2 задач(и): пересчёт карт сменил бы учёт устройств")
    ответ = client(hub).post("/admin/agents/GTX/rescan", headers=ADMIN)

    assert ответ.status_code == 409
    assert "2 задач" in ответ.json()["error"]["message"]


def test_без_токена_не_пускает():
    """Перезапуск чужой машины — не то, что делают без предъявления прав."""
    assert client(Hub()).post("/admin/agents/GTX/restart").status_code in (401, 403)


# ------------------------------------------------- то же самое ниже, у хаба
def _hub_with_node(node_id: str = "GTX"):
    from looma.orchestrator.agents import AgentHub, AgentNode, AgentSession
    from looma.proto_gen import agent_pb2 as pb

    hub = AgentHub()
    hub.sessions[node_id] = AgentSession(AgentNode(
        node_id=node_id, accepts_tasks=True,
        hardware=pb.Hardware(num_gpus=0, device="cpu", gpu_name="x86_64")))
    return hub


def test_свежее_железо_из_телеметрии_заменяет_старое():
    """Раньше железо приезжало только при регистрации и потом не менялось
    никогда: карта, пропавшая вместе с драйвером или вернувшаяся с ним,
    оставалась в панели такой, какой была в минуту подключения."""
    from looma.proto_gen import agent_pb2 as pb

    hub = _hub_with_node()
    hub.on_telemetry(pb.Telemetry(
        node_id="GTX",
        hardware=pb.Hardware(num_gpus=2, device="cuda", gpu_name="RTX 4090")))

    node = hub.sessions["GTX"].node
    assert node.hardware.device == "cuda"
    assert node.hardware.gpu_name == "RTX 4090"


def test_агент_постарше_не_обнуляет_карточку_узла():
    """Он этого поля не заполняет вовсе. Подставить вместо него пустое железо
    значит превратить исправный узел в машину без карт и без имени."""
    from looma.proto_gen import agent_pb2 as pb

    hub = _hub_with_node()
    hub.on_telemetry(pb.Telemetry(node_id="GTX", gpus_total=0))

    assert hub.sessions["GTX"].node.hardware.gpu_name == "x86_64"


@pytest.mark.asyncio
async def test_отказ_узла_доезжает_до_того_кто_ждёт():
    """Ack с ok=false — такой же ответ, как согласие. Пока он только писался в
    лог, ожидающий висел до таймаута и видел «узел не ответил вовремя» вместо
    настоящей причины."""
    import asyncio

    from looma.orchestrator.agents import AgentError
    from looma.proto_gen import agent_pb2 as pb

    hub = _hub_with_node()
    waiter = asyncio.get_running_loop().create_future()
    hub._pending_acks["c1"] = waiter
    hub.on_ack(pb.Ack(command_id="c1", ok=False, error="на узле 2 задач(и)"))

    with pytest.raises(AgentError, match="2 задач"):
        await waiter
