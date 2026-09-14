"""Агент не должен снимать собственную группу процессов.

Задача уходит в свою группу через setsid, и на этом держалось всё: снимая
группу задачи, агент «не может» попасть по себе. Но preexec выполняется после
fork в многопоточном процессе, и не отработай он однажды — `_group_of` вернёт
группу агента, а первое же снятие задачи убьёт узел вместе со всеми его
остальными задачами. Такое нельзя оставлять на комментарий.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.tasks import runner


def test_своя_группа_не_снимается(monkeypatch):
    послано = []
    monkeypatch.setattr(runner.os, "killpg", lambda g, s: послано.append((g, s)))

    runner._signal_group(os.getpgid(0), 15)

    assert послано == [], "агент отправил сигнал собственной группе"


def test_чужая_группа_снимается(monkeypatch):
    послано = []
    monkeypatch.setattr(runner.os, "killpg", lambda g, s: послано.append((g, s)))

    runner._signal_group(os.getpgid(0) + 12345, 15)

    assert послано == [(os.getpgid(0) + 12345, 15)]


def test_исчезнувшая_группа_не_ошибка(monkeypatch):
    def нет_такой(g, s):
        raise ProcessLookupError()

    monkeypatch.setattr(runner.os, "killpg", нет_такой)
    runner._signal_group(os.getpgid(0) + 12345, 15)      # не должно бросить
