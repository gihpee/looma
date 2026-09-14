"""gRPC внутри задач и внутри агента обязан переживать fork.

Со стенда (nv2, лог прокси клиентского сервера Ray):

    fork_posix.cc:71] Other threads are currently calling into gRPC, skipping fork() handlers
    F ... ev_epoll1_linux.cc:1121] Check failed: next_worker->state == KICKED
    ERROR proxier.py:394 -- SpecificServer startup failed

Прокси форкает под клиента отдельный процесс, будучи работающим gRPC-сервером.
С движком epoll1 (умолчание на Linux) ребёнок получает живое состояние epoll и
падает на внутренней проверке gRPC — молча, с пустым логом, потому что до
Python дело не доходит. Первое подключение к кластеру отказывало, повтор
проходил: гонка.

Движок `poll` — единственный, для которого gRPC обещает безопасный fork.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_агент_ставит_безопасный_fork_до_импорта_grpc():
    """Порядок важен: движок опроса gRPC выбирает при загрузке модуля."""
    import looma_agent  # noqa: F401  — __init__ пакета

    assert os.environ.get("GRPC_ENABLE_FORK_SUPPORT") == "1"
    assert os.environ.get("GRPC_POLL_STRATEGY") == "poll"


def test_задача_получает_безопасный_fork_явно():
    """Не наследованием, а явно: окружение задачи собирается с нуля."""
    from looma_agent.tasks.directory import TaskDirectory
    from looma_agent.tasks.limits import Isolation
    from looma_agent.tasks.runner import Task
    from looma_agent.tasks.spec import Resources, TaskSpec

    task = Task(
        spec=TaskSpec(task_id="t1", command=["true"], resources=Resources()),
        directory=TaskDirectory(root=Path("/tmp/looma-test-t1")),
        isolation=Isolation(uid=None, gid=None, user="nobody"),
    )

    env = task.process_env()

    assert env["GRPC_ENABLE_FORK_SUPPORT"] == "1"
    assert env["GRPC_POLL_STRATEGY"] == "poll"


def test_выбор_оператора_не_перебивается(monkeypatch):
    """setdefault: оператор, знающий, что делает, остаётся прав."""
    monkeypatch.setenv("GRPC_POLL_STRATEGY", "epoll1")
    import importlib

    import looma_agent
    importlib.reload(looma_agent)

    assert os.environ["GRPC_POLL_STRATEGY"] == "epoll1"
