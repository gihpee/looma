"""Ручка, которая размещает Ray-кластер.

Ничего специфичного для Ray в оркестраторе нет и не должно быть: это обычная
группа с обычным окружением. Проверяется, что она собрана правильно — и что
она честно отказывает там, где ещё не работает.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from looma.orchestrator.payloads import PayloadMissing, collect, ray_payload
from test_agent_pipeline import two_nodes  # noqa: F401  (фикстура)


# ------------------------------------------------------------------ нагрузка
def test_код_ранга_уезжает_вместе_с_задачей():
    payload = ray_payload()
    assert "looma_ray/server.py" in payload
    assert "looma_ray/cluster.py" in payload
    assert "looma_ray/ports.py" in payload
    assert all(name.startswith("looma_ray/") for name in payload)


def test_обе_нагрузки_ищутся_одинаково():
    """Инференс здесь ничем не привилегирован — он просто первый жилец."""
    from looma.orchestrator.models import stage_payload

    assert set(ray_payload()) & set(stage_payload()) == set()


def test_пустой_каталог_с_подходящим_именем_не_считается_нагрузкой(tmp_path):
    """Такой встречается чаще, чем хотелось бы: остаток от переименования."""
    (tmp_path / "looma_ray" / "looma_ray").mkdir(parents=True)
    with pytest.raises(PayloadMissing) as exc:
        collect("looma_ray", human="кода ранга Ray",
                dirs=[tmp_path / "looma_ray" / "looma_ray"])
    assert "Искали в" in str(exc.value)


# -------------------------------------------------------------------- ручка
def client(hub):
    from fastapi.testclient import TestClient

    from looma.api.app import create_app
    from test_agent_gateway import ADMIN_HEADERS, _Settings

    return TestClient(create_app(agents=hub, config=_Settings()), headers=ADMIN_HEADERS)


def test_кластер_ложится_на_несколько_узлов(two_nodes):
    """Раньше здесь стоял честный отказ: ранги на разных узлах друг друга не
    видели. Теперь между ними есть проброс портов, и группа размещается."""
    hub = two_nodes.hub
    answer = client(hub).post("/admin/ray", json={"size": 2, "label": "пара"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["size"] == 2
    assert sorted(body["nodes"]) == ["node-0", "node-1"]

    record = hub.groups[body["group_id"]]
    assert sorted(record.nodes.values()) == ["node-0", "node-1"]
    # Команда одна на всех: свой ранг задача узнаёт из окружения.
    for rank in record.tasks:
        assert hub.tasks[record.tasks[rank]].command[:3] == [
            "python", "-m", "looma_ray.server"]


def test_узлов_просят_больше_чем_есть(two_nodes):
    answer = client(two_nodes.hub).post("/admin/ray", json={"size": 5})
    assert answer.status_code == 409
    assert "5" in answer.json()["error"]["message"]


def test_на_одном_узле_группа_собирается_правильно(two_nodes):
    hub = two_nodes.hub
    answer = client(hub).post("/admin/ray", json={
        "node_ids": ["node-0"],
        "script": base64.b64encode(b"import ray\n").decode(),
        "label": "проба",
    })
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["size"] == 1
    assert body["entry"] == "job.py"
    assert body["nodes"] == ["node-0"]

    # Группа — обычная: то же размещение, тот же label, та же готовность.
    record = hub.groups[body["group_id"]]
    assert record.label == "проба"
    task = hub.tasks[record.tasks[0]]
    assert task.command[:3] == ["python", "-m", "looma_ray.server"]
    assert "--script" in task.command and "job.py" in task.command


def test_без_скрипта_кластер_просто_стоит(two_nodes):
    answer = client(two_nodes.hub).post("/admin/ray", json={"node_ids": ["node-1"]})
    assert answer.status_code == 200, answer.text
    assert answer.json()["entry"] == ""
    task = two_nodes.hub.tasks[
        two_nodes.hub.groups[answer.json()["group_id"]].tasks[0]]
    assert "--script" not in task.command


def test_испорченный_скрипт_называет_причину(two_nodes):
    answer = client(two_nodes.hub).post("/admin/ray", json={
        "node_ids": ["node-0"], "script": "это не base64 !!!"})
    assert answer.status_code == 400
    assert "base64" in answer.json()["error"]["message"]


def test_неподключённый_узел_назван_поимённо(two_nodes):
    answer = client(two_nodes.hub).post("/admin/ray",
                                        json={"node_ids": ["которого-нет"]})
    assert answer.status_code == 409
    assert "которого-нет" in answer.json()["error"]["message"]


# ---------------------------------------------------------------- связность
def test_ответ_называет_путь_которым_соберётся_кластер(two_nodes):
    """Медленный кластер должен быть объяснимым, а не загадочным."""
    answer = client(two_nodes.hub).post("/admin/ray", json={"size": 2})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["path"] in ("direct", "relay")
    assert "relayed_pairs" in body


def test_безнадёжная_пара_без_реле_отвергается_до_занятия_карт(two_nodes, monkeypatch):
    """Единственный случай, когда Ray действительно не поедет. Молча пустить
    его туда — значит показать зависание вместо причины."""
    monkeypatch.setattr("looma.api.app.relay_addrs", lambda: [])
    for session in two_nodes.hub.sessions.values():
        session.node.reachable = False
        session.node.symmetric_nat = True

    before = len(two_nodes.hub.groups)
    answer = client(two_nodes.hub).post("/admin/ray", json={"size": 2})
    assert answer.status_code == 409
    text = answer.json()["error"]["message"]
    assert "реле не развёрнуто" in text
    assert "node-0" in text and "node-1" in text
    assert len(two_nodes.hub.groups) == before, "группа не должна была появиться"


def test_с_реле_та_же_группа_поедет(two_nodes, monkeypatch):
    """Отказ — только про невозможность, а не про медленность."""
    monkeypatch.setattr("looma.api.app.relay_addrs", lambda: ["/ip4/1.2.3.4/tcp/47200"])
    for session in two_nodes.hub.sessions.values():
        session.node.reachable = False
        session.node.symmetric_nat = True

    answer = client(two_nodes.hub).post("/admin/ray", json={"size": 2})
    assert answer.status_code == 200, answer.text
    assert answer.json()["path"] == "relay"
    assert answer.json()["warning"]


def test_узлы_подбираются_по_связности(two_nodes, monkeypatch):
    """При выборе «самых свободных» связность важнее памяти: через реле у Ray
    идёт весь обмен, а не восемь килобайт на токен."""
    monkeypatch.setattr("looma.api.app.relay_addrs", lambda: ["/ip4/1.2.3.4/tcp/47200"])
    nodes = list(two_nodes.hub.sessions.values())
    nodes[0].node.symmetric_nat, nodes[0].node.reachable = True, False
    nodes[1].node.symmetric_nat, nodes[1].node.reachable = False, True

    answer = client(two_nodes.hub).post("/admin/ray", json={"size": 1})
    assert answer.status_code == 200, answer.text
    assert answer.json()["nodes"] == [nodes[1].node.node_id]


def test_узел_названный_дважды_получает_два_ранга(two_nodes):
    """На что опирается форма: собрать кластер там, где сеть между рангами не
    нужна вовсе. Соседи оказываются локальными, проброс не включается."""
    hub = two_nodes.hub
    answer = client(hub).post("/admin/ray", json={
        "node_ids": ["node-0", "node-0"], "label": "вдвоём-на-одном"})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["size"] == 2
    assert body["nodes"] == ["node-0", "node-0"]
    # Связность тут ни при чём: узел с самим собой встречается всегда.
    assert body["path"] == "direct"

    record = hub.groups[body["group_id"]]
    assert set(record.nodes.values()) == {"node-0"}
    assert len(record.tasks) == 2
    # Оба ранга получают одну команду: свой номер задача берёт из окружения.
    assert (hub.tasks[record.tasks[0]].command[:3]
            == hub.tasks[record.tasks[1]].command[:3]
            == ["python", "-m", "looma_ray.server"])


# ------------------------------------------------------- законченные группы
def test_законченная_группа_помечена_как_законченная(two_nodes):
    """Иначе панель показывает её среди работающих — как и было на стенде,
    где три мёртвых кластера висели в разделе «работают»."""
    hub = two_nodes.hub
    body = client(hub).post("/admin/ray", json={"node_ids": ["node-0"]}).json()
    record = hub.groups[body["group_id"]]

    listed = client(hub).get("/admin/groups").json()["groups"][0]
    assert listed["finished"] is False

    for task_id in record.tasks.values():
        hub.tasks[task_id].state = "failed"
    assert client(hub).get("/admin/groups").json()["groups"][0]["finished"] is True


def test_группу_можно_убрать_совсем(two_nodes):
    """Остановка этого не делает намеренно: у остановленной задачи ещё лежит
    результат. Забытая не нужна никому — и без этого записи копились вечно."""
    hub = two_nodes.hub
    body = client(hub).post("/admin/ray", json={"node_ids": ["node-0", "node-0"]}).json()
    group_id = body["group_id"]
    tasks = list(hub.groups[group_id].tasks.values())

    answer = client(hub).delete(f"/admin/groups/{group_id}")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"forgotten": group_id, "tasks": 2}
    assert group_id not in hub.groups
    assert all(t not in hub.tasks for t in tasks)


def test_убрать_несуществующую_группу_нельзя(two_nodes):
    answer = client(two_nodes.hub).delete("/admin/groups/которой-нет")
    assert answer.status_code == 404
    assert "которой-нет" in answer.json()["error"]["message"]


def test_законченные_убираются_сами_через_сутки(two_nodes):
    """Иначе через месяц работы панель показывает историю вместо состояния."""
    import time as _time

    hub = two_nodes.hub
    body = client(hub).post("/admin/ray", json={"node_ids": ["node-0"]}).json()
    record = hub.groups[body["group_id"]]
    for task_id in record.tasks.values():
        hub.tasks[task_id].state = "failed"

    assert hub.prune() == 0, "свежую группу убирать рано — за результатом придут"
    record.submitted_at = _time.time() - 25 * 3600
    assert hub.prune() == 1
    assert body["group_id"] not in hub.groups


def test_работающая_группа_не_убирается_по_возрасту(two_nodes):
    """Долгоживущий кластер — это норма, а не забытая запись."""
    import time as _time

    hub = two_nodes.hub
    body = client(hub).post("/admin/ray", json={"node_ids": ["node-0"]}).json()
    record = hub.groups[body["group_id"]]
    for task_id in record.tasks.values():
        hub.tasks[task_id].state = "running"
    record.submitted_at = _time.time() - 400 * 3600

    assert hub.prune() == 0
    assert body["group_id"] in hub.groups


def test_кластер_ставит_ray_с_клиентским_входом(two_nodes, monkeypatch):
    """Серверная часть Ray Client лежит в extra `client`. Без него
    `--ray-client-server-port` не игнорируется, а роняет `ray start` целиком —
    то есть кластер не поднимется вовсе."""
    hub = two_nodes.hub
    asked = {}
    original = hub.submit_group

    def watch(**kwargs):
        asked.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(hub, "submit_group", watch)
    answer = client(hub).post("/admin/ray", json={"node_ids": ["node-0"]})
    assert answer.status_code == 200, answer.text
    assert asked["environment"]["requirements"] == ["ray[client]"]

    client(hub).post("/admin/ray", json={"node_ids": ["node-1"],
                                         "ray_version": "2.58.0"})
    assert asked["environment"]["requirements"] == ["ray[client]==2.58.0"]


def test_просимые_библиотеки_доезжают_до_узлов(two_nodes, monkeypatch):
    """Через ту же дорогу, что и сам Ray: одно окружение на узел, поставленное
    до запуска. Ставить их потом было бы некуда — воркеры Ray наследуют то,
    что уже есть."""
    hub = two_nodes.hub
    asked = {}
    original = hub.submit_group

    def watch(**kwargs):
        asked.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(hub, "submit_group", watch)
    answer = client(hub).post("/admin/ray", json={
        "node_ids": ["node-0"], "requirements": "torch\nnumpy"})

    assert answer.status_code == 200, answer.text
    assert asked["environment"]["requirements"] == ["ray[client]", "torch", "numpy"]


def test_негодные_требования_отвергаются_до_запуска(two_nodes):
    """После запуска это стоило бы установки окружения на чужой машине и
    отказа, который человек увидел бы минутами позже и в логе задачи."""
    answer = client(two_nodes.hub).post("/admin/ray", json={
        "node_ids": ["node-0"], "requirements": ["--index-url https://example.invalid"]})

    assert answer.status_code == 400
    assert "флаги pip" in answer.json()["error"]["message"]


# ------------------------------------------------------- библиотеки клиента
def test_библиотеки_клиента_едут_вместе_с_ray():
    """Без этого клиент получал голый Ray и не мог привезти ни торч, ни свою
    библиотеку — а распараллеливать чем-то надо. Механизм окружений умеет это
    давно; к форме Ray он просто не был подключён."""
    from looma.api.app import _requirements_of

    assert _requirements_of("torch\nnumpy>=1.2") == ["torch", "numpy>=1.2"]


def test_requirements_txt_копируется_целиком():
    """Человек копирует свой файл как есть. Отвергать его из-за комментария
    или пустой строки — придирка, из-за которой он пойдёт их вычищать вручную."""
    from looma.api.app import _requirements_of

    assert _requirements_of("# нужное\ntorch\n\n  numpy  \n") == ["torch", "numpy"]


def test_флаги_pip_не_принимаются():
    """Они меняют не пакет, а откуда и как он ставится, — вплоть до чужого
    индекса. На чужой машине это решает её владелец, а не арендатор."""
    import pytest as _pytest

    from looma.api.app import _requirements_of

    with _pytest.raises(ValueError, match="флаги pip"):
        _requirements_of(["--index-url", "https://example.invalid"])


def test_список_принимается_наравне_с_текстом():
    """Форма шлёт текст в столбик, а те, кто ходит в API напрямую, — список."""
    from looma.api.app import _requirements_of

    assert _requirements_of(["torch"]) == _requirements_of("torch") == ["torch"]
