"""Снятие задачи убирает ВСЁ её: процессы, порты, каталоги. Окружение остаётся.

Со стенда, на двух узлах: по три каталога /tmp/looma-* от кластеров за два
дня; воркер мёртвого кластера Ray на порту 30711, из-за которого следующий
стартовал с «Address already in use». Уборка шла по группе процессов, а Ray
делает `os.setpgrp()` намеренно и уводит raylet, GCS и воркеров в свою — по
группе до них не дотянуться никогда.

Единственное, что потомок не может с себя снять, уходя в другую группу, —
окружение. LOOMA_TASK_ID получает каждый процесс задачи и передаёт всем своим.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.tasks import runner

pytest.importorskip("psutil")


def беглец(task_id: str) -> subprocess.Popen:
    """Процесс, который уходит из группы так же, как Ray: setpgrp и спит."""
    return subprocess.Popen(
        [sys.executable, "-c",
         "import os, time; os.setpgrp(); time.sleep(60)"],
        env={**os.environ, "LOOMA_TASK_ID": task_id},
        start_new_session=True,
    )


def жив(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def test_процесс_вне_группы_находится_по_метке():
    task_id = "group-test-r0"
    proc = беглец(task_id)
    try:
        time.sleep(0.3)
        assert proc.pid in runner._processes_of(task_id)
        assert proc.pid not in runner._processes_of("group-другая-r1")
    finally:
        proc.kill()


def test_агент_и_его_предки_не_попадают_под_метку(monkeypatch):
    """Метки у нас нет, но проверка стоит дёшево, а ошибка стоила бы узла."""
    monkeypatch.setenv("LOOMA_TASK_ID", "group-test-r0")
    assert os.getpid() not in runner._processes_of("group-test-r0")


def test_уборка_дотягивается_до_ушедших_из_группы():
    """Главное. Ровно то, чего не делала уборка по группе."""
    task_id = "group-teardown-r0"
    proc = беглец(task_id)
    time.sleep(0.3)
    assert жив(proc)

    task = runner.Task.__new__(runner.Task)
    task.spec = type("S", (), {"task_id": task_id})()
    task._group = None                        # группа задачи уже пуста

    task._reap_group()

    for _ in range(30):
        if not жив(proc):
            break
        time.sleep(0.1)
    assert not жив(proc), "процесс вне группы пережил уборку"
    assert runner._processes_of(task_id) == []


def test_уборка_не_трогает_чужие_задачи():
    свой = беглец("group-a-r0")
    чужой = беглец("group-b-r0")
    time.sleep(0.3)
    try:
        task = runner.Task.__new__(runner.Task)
        task.spec = type("S", (), {"task_id": "group-a-r0"})()
        task._group = None
        task._reap_group()

        for _ in range(30):
            if not жив(свой):
                break
            time.sleep(0.1)
        assert not жив(свой)
        assert жив(чужой), "уборка одной задачи убила другую"
    finally:
        чужой.kill()


def test_уборка_при_старте_снимает_всех_с_меткой_и_чистит_каталоги(tmp_path):
    from looma_agent.tasks.directory import _scratch_name
    from looma_agent.tasks.registry import TaskRegistry

    сирота = беглец("group-orphan-r0")
    time.sleep(0.3)
    (tmp_path / "group-orphan-r0" / "work").mkdir(parents=True)
    short = Path("/tmp") / _scratch_name("group-orphan-r0")
    short.mkdir(exist_ok=True)
    (short / "session_x").mkdir(exist_ok=True)

    reg = TaskRegistry.__new__(TaskRegistry)
    reg.root = tmp_path
    try:
        итог = reg.sweep_leftovers()

        for _ in range(30):
            if not жив(сирота):
                break
            time.sleep(0.1)
        assert not жив(сирота), "сирота пережил уборку при старте"
        assert not (tmp_path / "group-orphan-r0").exists()
        assert not short.exists()
        assert итог["processes"] >= 1
        assert итог["directories"] >= 1
    finally:
        сирота.kill()
        import shutil
        shutil.rmtree(short, ignore_errors=True)


def test_снятая_задача_освобождается_сразу_а_не_через_час(tmp_path, monkeypatch):
    """Результата, за которым придут, у снятой задачи нет."""
    import threading

    from looma_agent.tasks.registry import TaskRegistry

    reg = TaskRegistry.__new__(TaskRegistry)
    reg.retention_s = 3600
    reg._lock = threading.RLock()
    reg._tasks = {}
    reg._held = {}
    reg.environments = type("E", (), {"release": lambda self, fp: None})()
    reg.models = None

    освобождали = []
    reg.release = lambda task_id: освобождали.append(task_id)
    reg._release_devices = lambda task_id: None

    task = runner.Task.__new__(runner.Task)
    task.spec = type("S", (), {"task_id": "group-cancelled-r0"})()
    task.environment = type("Env", (), {"fingerprint": "fp"})()
    task.state = runner.CANCELLED
    task._done = threading.Event(); task._done.set()
    task.wait = lambda timeout=None: True
    task.released = threading.Event()

    начали = time.monotonic()
    reg._reap(task)

    assert освобождали == ["group-cancelled-r0"]
    assert time.monotonic() - начали < 1.0, "снятая задача ждала удержания"


def test_явное_освобождение_будит_удержание_сразу():
    """Поток удержания не должен спать час после того, как убирать нечего."""
    import threading

    from looma_agent.tasks.registry import TaskRegistry

    reg = TaskRegistry.__new__(TaskRegistry)
    reg.retention_s = 3600
    reg.environments = type("E", (), {"release": lambda self, fp: None})()
    reg._release_devices = lambda task_id: None
    reg.get = lambda task_id: None
    reg.release = lambda task_id: None

    task = runner.Task.__new__(runner.Task)
    task.spec = type("S", (), {"task_id": "group-done-r0"})()
    task.environment = type("Env", (), {"fingerprint": "fp"})()
    task.state = runner.DONE
    task.wait = lambda timeout=None: True
    task.released = threading.Event()

    threading.Timer(0.2, task.released.set).start()
    начали = time.monotonic()
    reg._reap(task)
    assert time.monotonic() - начали < 2.0, "удержание не проснулось по событию"
