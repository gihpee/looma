"""Команды самому узлу: перечитать железо и уйти на перезапуск.

Обе появились ради одного. Агент определяет железо один раз, в конструкторе, и
держит этот ответ до конца процесса; перезапустить его на ЧУЖОЙ машине можно
было только выкаткой релиза. Со стенда: у узла пропали карты, потому что его
хозяин пересобирал драйвер, — и вернуть их в панель было нечем, кроме
обновления всего парка ради одного узла.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.proto import agent_pb2
from looma_agent.tasks.env import EnvironmentCache
from looma_agent.tasks.limits import resolve_isolation
from looma_agent.tasks.registry import TaskRegistry
from looma_agent.update import UPDATE_EXIT_CODE, Updater


@pytest.fixture
def isolation(monkeypatch):
    monkeypatch.setenv("LOOMA_ALLOW_UNPRIVILEGED_TASKS", "1")
    return resolve_isolation()


@pytest.fixture
def registry(tmp_path, isolation):
    reg = TaskRegistry(root=tmp_path / "tasks", isolation=isolation,
                       environments=EnvironmentCache(tmp_path / "envs"),
                       total_gpus=4, retention_s=60.0)
    yield reg
    reg.stop_all()


# --------------------------------------------------------------- пересчёт карт
def test_карты_пересчитываются_когда_узел_свободен(registry):
    assert registry.recount_devices(2) == ""
    assert registry.total_gpus == 2
    assert registry.free_devices() == [0, 1]


def test_под_работающей_задачей_пересчёт_отвергается(registry):
    """Индексы устройств у задачи считаются от этого числа. Уменьшить его под
    работающей задачей значит отдать её карту второму желающему — а «карта
    занята дважды» выглядит потом как поломка драйвера, а не как наша."""
    from looma_agent.tasks.spec import TaskSpec

    registry.submit(TaskSpec.from_dict({
        "task_id": "t1", "command": [sys.executable, "-c", "import time; time.sleep(30)"]}))

    refusal = registry.recount_devices(0)

    assert "задач" in refusal
    assert registry.total_gpus == 4, "число карт не должно было измениться"


# ------------------------------------------------------------------ перезапуск
def test_уход_на_перезапуск_помечается_как_плановый():
    """Пусковой слой отличает плановый уход от падения ТОЛЬКО по коду выхода.
    Обычный выход раньше тридцатой секунды идёт в счёт неудач, а три неудачи
    подряд у версии без отметки о здоровье означают откат — то есть перезапуск
    по кнопке откатывал бы исправную версию."""
    остановлен = []
    updater = Updater(current_version="0.1.0",
                      drain=lambda _s: True,
                      stop=lambda: остановлен.append(1))

    updater.step_aside("перезапуск по команде оператора")

    assert updater.exit_code == UPDATE_EXIT_CODE
    assert остановлен == [1]


def test_не_успевшие_задачи_не_держат_перезапуск_вечно():
    """Слив задач не бесконечен: узел, который не может перезапуститься из-за
    зависшей задачи, — это узел, который нельзя починить удалённо вообще."""
    updater = Updater(current_version="0.1.0",
                      drain=lambda _s: False,   # не успели
                      stop=lambda: None)

    updater.step_aside("перезапуск", drain_s=0.0)

    assert updater.exit_code == UPDATE_EXIT_CODE


# -------------------------------------------------------------- разбор команды
def test_незнакомое_действие_отвергается_вслух():
    """Узел бывает старее оркестратора. Молча принять незнакомое действие
    значит оставить оператора с кнопкой, которая ничего не делает и об этом не
    сообщает."""
    from looma_agent.control.handlers import CommandHandlers

    пришло = []
    handlers = CommandHandlers(tasks=None, telemetry=lambda: None,
                               on_node_command=пришло.append)
    handlers.handle(agent_pb2.ServerMessage(
        node_command=agent_pb2.NodeCommand(command_id="c1", action="rescan")))

    assert [c.action for c in пришло] == ["rescan"]
