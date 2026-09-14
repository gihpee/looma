"""Узел, который замолчал, обязан сам сказать, где он стоит.

Четыре раза подряд агент зависал одинаково снаружи: процесс жив, контейнер up,
наверх не идёт ничего. Каждый раз причина оказывалась РАЗНОЙ — снятие задачи
прямо в приёмном потоке gRPC, два ожидающих на один waitpid, заклинивший
драйвер, — и каждый раз её ловили py-spy по живому процессу, то есть человек
должен был оказаться у машины в нужную минуту.

Сторож снимает стек сам, в момент события.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.main import Agent, _thread_dump


def _агент(sent_ago: float) -> Agent:
    agent = Agent.__new__(Agent)
    agent._stop = threading.Event()
    agent._sent_at = time.monotonic() - sent_ago
    agent._dumped_at = 0.0
    return agent


def test_снимок_содержит_все_потоки():
    держим = threading.Event()
    поток = threading.Thread(target=lambda: держим.wait(10),
                             name="приметный-поток", daemon=True)
    поток.start()
    time.sleep(0.05)

    строки = "\n".join(_thread_dump())

    assert "приметный-поток" in строки
    assert "MainThread" in строки
    держим.set()


def test_снимок_ничего_не_блокирует():
    """Сторож обязан работать именно тогда, когда всё остальное стоит: он не
    берёт ни одного замка агента и не заходит в чужие рантаймы."""
    начали = time.monotonic()
    _thread_dump()
    assert time.monotonic() - начали < 0.5


def test_пока_доклады_уходят_сторож_молчит(monkeypatch, caplog):
    from looma_agent import main as main_mod

    monkeypatch.setattr(main_mod, "WATCHDOG_INTERVAL_S", 0.05)
    monkeypatch.setattr(main_mod, "STALL_AFTER_S", 60)
    agent = _агент(sent_ago=0)

    поток = threading.Thread(target=agent._watchdog_loop, daemon=True)
    поток.start()
    time.sleep(0.3)
    agent._stop.set()
    поток.join(1)

    assert "снимаю стек" not in caplog.text


def test_молчание_дольше_порога_печатает_стек(monkeypatch, caplog):
    import logging

    from looma_agent import main as main_mod

    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(main_mod, "WATCHDOG_INTERVAL_S", 0.05)
    monkeypatch.setattr(main_mod, "STALL_AFTER_S", 0.01)
    monkeypatch.setattr(main_mod, "DUMP_EVERY_S", 300)
    agent = _агент(sent_ago=120)

    поток = threading.Thread(target=agent._watchdog_loop, daemon=True)
    поток.start()
    time.sleep(0.3)
    agent._stop.set()
    поток.join(1)

    assert "наверх не уходило" in caplog.text
    assert "поток" in caplog.text


def test_стек_печатается_не_чаще_чем_раз_в_период(monkeypatch, caplog):
    """Залипание может длиться часами, и лог не должен стать его главным
    следствием."""
    import logging

    from looma_agent import main as main_mod

    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(main_mod, "WATCHDOG_INTERVAL_S", 0.02)
    monkeypatch.setattr(main_mod, "STALL_AFTER_S", 0.01)
    monkeypatch.setattr(main_mod, "DUMP_EVERY_S", 300)
    agent = _агент(sent_ago=120)

    поток = threading.Thread(target=agent._watchdog_loop, daemon=True)
    поток.start()
    time.sleep(0.4)
    agent._stop.set()
    поток.join(1)

    assert caplog.text.count("наверх не уходило") == 1


def test_стек_можно_спросить_сигналом(tmp_path):
    """Ставить py-spy на чужую машину — не то, о чём стоит просить владельца
    узла: на nv3 это и не получилось, sudo не увидел conda-окружения. Сигнал
    есть везде и ничего не требует."""
    import faulthandler
    import signal

    from looma_agent.main import arm_stack_signal

    было = faulthandler.is_enabled()
    подсказка = arm_stack_signal()
    try:
        assert "kill -USR1" in подсказка
        assert str(__import__("os").getpid()) in подсказка
        assert faulthandler.unregister(signal.SIGUSR1) is True
    finally:
        if not было:
            faulthandler.disable()
