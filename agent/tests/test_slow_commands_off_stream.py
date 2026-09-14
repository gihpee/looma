"""Медленная команда не должна затыкать приём остальных.

С дампа живого агента на маке (py-spy): главный поток стоял в снятии задачи —

    _kill (tasks/runner.py) <- stop (control/tasks.py) <- handle (handlers.py)
    <- _receive (control/client.py) <- _serve <- run_forever

то есть приёмный цикл управляющего потока выполнял снятие ПРЯМО в себе. Пока
оно идёт, узел не разбирает ни одной другой команды и не отвечает даже на
запрос логов — снаружи неотличим от мёртвого. Ровно об этом и написано в
докстринге самого модуля, но для снятия и освобождения это не соблюдалось.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.control.handlers import SLOW, CommandHandlers
from looma_agent.proto import agent_pb2


class Задачи:
    def __init__(self) -> None:
        self.держим = threading.Event()
        self.начали = threading.Event()
        self.быстрых = 0

    def stop(self, _command) -> None:
        self.начали.set()
        self.держим.wait(30)

    def release(self, _command) -> None:
        self.начали.set()
        self.держим.wait(30)

    def input_chunk(self, _chunk) -> None:
        self.быстрых += 1


def обработчики(задачи) -> CommandHandlers:
    return CommandHandlers(tasks=задачи, telemetry=lambda: None)


def test_снятие_не_держит_приёмный_поток():
    задачи = Задачи()
    руки = обработчики(задачи)

    начали = time.monotonic()
    руки.handle(agent_pb2.ServerMessage(
        stop_task=agent_pb2.StopTask(command_id="c1", task_id="t1")))
    ушло = time.monotonic() - начали

    assert ушло < 1.0, "снятие выполнялось прямо в приёмном потоке"
    assert задачи.начали.wait(2), "снятие не запустилось вовсе"
    задачи.держим.set()


def test_пока_снятие_идёт_другие_команды_разбираются():
    """То, ради чего всё это: узел обязан отвечать, пока убирает за задачей."""
    задачи = Задачи()
    руки = обработчики(задачи)

    руки.handle(agent_pb2.ServerMessage(
        stop_task=agent_pb2.StopTask(command_id="c1", task_id="t1")))
    assert задачи.начали.wait(2)

    for _ in range(5):
        руки.handle(agent_pb2.ServerMessage(
            input_chunk=agent_pb2.InputChunk(task_id="t2", name="x")))

    assert задачи.быстрых == 5
    задачи.держим.set()


def test_быстрые_команды_остаются_в_порядке():
    """Куски входных файлов и куски туннеля обязаны прийти в том же порядке, в
    каком отправлены. Уводить их в потоки нельзя."""
    assert "input_chunk" not in SLOW
    assert "tunnel_chunk" not in SLOW
    assert SLOW == {"stop_task", "release_task"}
