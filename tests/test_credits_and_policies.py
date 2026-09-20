"""Этап 8 контракта: стоимость токенов, отчёт строками, очередь ожидания
аренды, логотипы с HuggingFace. Без базы и без сети — всё, что тут может
сломаться, ломается в арифметике и в порядке, а не в Postgres."""

from __future__ import annotations

import io
import json

from fastapi.testclient import TestClient

from looma.api.app import create_app
from looma.orchestrator import logos
from looma.orchestrator.config import OrchestratorConfig
from looma.orchestrator.waiting import CANCELLED, EXPIRED, STARTED, WAITING, WaitingRoom
from looma.usage.ledger import report_csv, token_cost

ADMIN = {"X-Looma-Admin-Token": "secret"}


# ---------------------------------------------------------- цена ответа
def test_стоимость_ответа_считается_за_миллион():
    assert token_cost(prompt_tokens=1_000_000, completion_tokens=0,
                      price_in=2000, price_out=8000) == 2000
    assert token_cost(prompt_tokens=500_000, completion_tokens=250_000,
                      price_in=2000, price_out=8000) == 1000 + 2000


def test_доля_копейки_округляется_вверх():
    """Короткий ответ стоит хотя бы копейку, если цена вообще есть: иначе
    чат из коротких реплик выходит бесплатным целиком."""
    assert token_cost(prompt_tokens=10, completion_tokens=10,
                      price_in=2000, price_out=8000) == 1


def test_без_цены_бесплатно():
    assert token_cost(prompt_tokens=10**6, completion_tokens=10**6,
                      price_in=0, price_out=0) == 0


# ------------------------------------------------------------ отчёт csv
def test_csv_по_строке_на_ресурс_и_модель_с_итогом():
    text = report_csv({
        "leases": [{"resource": "looma-compute", "leases": 2, "gpu_hours": 3.5,
                    "cost": 42000}],
        "tokens": [{"model": "Qwen3-32B", "prompt": 100, "completion": 50, "cost": 1}],
        "total": 42001,
    })
    rows = [line.split(";") for line in text.strip().split("\n")]
    assert rows[0][0] == "вид"
    assert rows[1][:2] == ["аренда", "looma-compute"] and rows[1][-1] == "420.00"
    assert rows[2][:2] == ["токены", "Qwen3-32B"] and rows[2][-1] == "0.01"
    assert rows[3][0] == "итого" and rows[3][-1] == "420.01"


# ----------------------------------------------------- очередь ожидания
def test_очередь_отдаёт_старших_первыми():
    clock = [1000.0]
    room = WaitingRoom(hours=1, now=lambda: clock[0])
    a = room.add(account_id=1, raw={"size": 2}, want=2, free=0)
    clock[0] += 1
    b = room.add(account_id=2, raw={"size": 1}, want=1, free=0)
    assert [t.id for t in room.due()] == [a.id, b.id]
    room.done(a.id, {"group_id": "g1", "granted": 2})
    assert [t.id for t in room.due()] == [b.id]
    assert room.of(1)[0].state == STARTED and room.of(1)[0].as_dict()["group_id"] == "g1"


def test_просроченная_заявка_закрывается_с_причиной():
    clock = [0.0]
    room = WaitingRoom(hours=1, now=lambda: clock[0])
    t = room.add(account_id=1, raw={}, want=1, free=0)
    clock[0] = 3601
    assert room.due() == []
    assert room.of(1)[0].state == EXPIRED and "не освободились" in room.of(1)[0].error


def test_чужую_заявку_отменить_нельзя():
    room = WaitingRoom()
    t = room.add(account_id=1, raw={}, want=1, free=0)
    assert not room.cancel(t.id, 2)
    assert room.cancel(t.id, 1)
    assert room.of(1)[0].state == CANCELLED
    assert len(room) == 0


def test_закрытая_заявка_забывается_через_час():
    clock = [0.0]
    room = WaitingRoom(hours=1, now=lambda: clock[0])
    t = room.add(account_id=1, raw={}, want=1, free=0)
    room.fail(t.id, "ошибка")
    clock[0] = 3601
    room.due()
    assert room.of(1) == []


# ---------------------------------------------------- политики по HTTP
class _Hub:
    """Пара узлов, один занят. Ровно столько, чтобы проверить политики."""

    def __init__(self):
        self.groups = {}

    def node_list(self):
        return [
            {"node_id": "a", "accepts_tasks": True, "tasks_running": 1},
            {"node_id": "b", "accepts_tasks": True, "tasks_running": 0},
        ]


def _client(tmp_path):
    config = OrchestratorConfig(data_dir=str(tmp_path), admin_token="secret")
    return TestClient(create_app(agents=_Hub(), config=config), headers=ADMIN)


def test_незнакомая_политика_отвергается(tmp_path):
    answer = _client(tmp_path).post("/api/compute", json={"size": 2, "policy": "magic"})
    assert answer.status_code == 400 and "policy" in answer.json()["error"]["message"]


def test_взять_сколько_есть_без_свободных_отказывает(tmp_path):
    class Busy(_Hub):
        def node_list(self):
            return [dict(n, tasks_running=1) for n in super().node_list()]

    config = OrchestratorConfig(data_dir=str(tmp_path), admin_token="secret")
    c = TestClient(create_app(agents=Busy(), config=config), headers=ADMIN)
    answer = c.post("/api/compute", json={"size": 2, "policy": "partial"})
    assert answer.status_code == 409 and "свободных узлов нет" in answer.json()["error"]["message"]


def test_ждать_без_учётной_записи_нельзя(tmp_path):
    """Аварийный вход по токену — не клиент: заявку некому вернуть."""
    answer = _client(tmp_path).post("/api/compute", json={"size": 2, "policy": "wait"})
    assert answer.status_code == 400


def test_очередь_клиента_пуста_и_чужая_заявка_не_видна(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/compute/pending").json() == {"pending": []}
    assert c.delete("/api/compute/pending/nope").status_code == 404


# ---------------------------------------------------------- логотипы HF
def test_владелец_репозитория():
    assert logos.owner_of("Qwen/Qwen3-8B") == "Qwen"
    assert logos.owner_of("Qwen3-8B") == ""
    assert logos.owner_of("/meta-llama/x/") == "meta-llama"


class _NotFound(OSError):
    code = 404


class _Reply:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self.body


async def test_аватар_организации_и_кэш():
    logos.forget()
    calls = []

    def opener(url, timeout):
        calls.append(url)
        if "/organizations/" in url:
            raise _NotFound("404")
        return _Reply(json.dumps({"avatarUrl": "https://cdn/x.jpeg"}).encode())

    assert await logos.avatar("someone/model", opener=opener) == "https://cdn/x.jpeg"
    assert await logos.avatar("someone/other", opener=opener) == "https://cdn/x.jpeg"
    # второй репозиторий того же владельца — из кэша, без сети
    assert len(calls) == 2
    assert logos.peek("someone/third") == "https://cdn/x.jpeg"
    logos.forget()


async def test_без_сети_логотипа_нет_и_это_не_ошибка():
    logos.forget()

    def opener(url, timeout):
        raise OSError("нет сети")

    assert await logos.avatar("Qwen/Qwen3-8B", opener=opener) is None
    logos.forget()


def test_публикация_прайса_не_ходит_в_сеть_за_известным_логотипом(tmp_path, monkeypatch):
    logos.forget()

    async def fake(repo, **_):
        return "https://cdn/qwen.png" if repo.startswith("Qwen/") else None

    monkeypatch.setattr(logos, "avatar", fake)
    c = _client(tmp_path)
    doc = {"models": [{"id": "Qwen3-32B", "repo": "Qwen/Qwen3-32B", "price_in": 1, "price_out": 1},
                      {"id": "custom", "logo_url": "https://mine/logo.svg", "price_in": 1, "price_out": 1},
                      {"id": "unknown", "price_in": 1, "price_out": 1}]}
    got = c.put("/admin/pricing", json=doc).json()["models"]
    assert got[0]["logo_url"] == "https://cdn/qwen.png"
    assert got[1]["logo_url"] == "https://mine/logo.svg"
    assert got[2]["logo_url"] is None
    assert c.get("/admin/logos/lookup", params={"repo": "Qwen/Qwen3-8B"}).json()["logo_url"] == "https://cdn/qwen.png"
