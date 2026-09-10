"""Удар сердца не должен зависеть от того, отвечает ли драйвер видеокарты.

Со стенда, узел nv3: процесс агента на месте, последний удар сердца 700 секунд
назад, наверх не уходит ничего. Замер свободной VRAM шёл ПРЯМО в цикле удара
сердца, а идёт он через pynvml — вызов в чужую библиотеку, у которого нет и не
может быть тайм-аута. Заклинивший драйвер (переустановка, Xid, зависшее ядро на
карте) останавливал весь цикл, и оркестратор списывал живую машину.

Ровно то же правило уже записано про p2p (p2p/layer.py): всё, что уходит в
чужой рантайм, меряется своим потоком, а удар сердца только читает готовое.
"""

from __future__ import annotations

import threading
import time

from looma_agent.main import Agent


class _Зависший:
    """Драйвер, который не отвечает никогда."""

    def __init__(self) -> None:
        self.вошли = threading.Event()

    def __call__(self) -> int:
        self.вошли.set()
        time.sleep(3600)
        return 0


def _голый_агент() -> Agent:
    """Агент без сети и без запуска: нужны только поля и методы замера."""
    agent = Agent.__new__(Agent)
    agent._stop = threading.Event()
    agent._sampling = threading.Event()
    agent._vram_at = 0.0
    agent.hardware = type("H", (), {"vram_free_bytes": 0})()
    return agent


def test_зависший_драйвер_держит_только_свой_поток(monkeypatch):
    driver = _Зависший()
    monkeypatch.setattr("looma_agent.main.free_vram_bytes", driver)
    agent = _голый_агент()

    threading.Thread(target=agent._sample_vram, daemon=True).start()
    assert driver.вошли.wait(2), "замер так и не начался"

    # Пока замер висит, всё остальное обязано отвечать мгновенно.
    начало = time.monotonic()
    agent._vram_stale_s()
    assert time.monotonic() - начало < 0.5


def test_второй_замер_не_заводится_поверх_зависшего(monkeypatch):
    """Иначе к зависшему потоку копятся новые — на том же самом месте."""
    agent = _голый_агент()
    agent._sampling.set()          # как будто замер уже идёт
    сколько = [0]

    def считать() -> int:
        сколько[0] += 1
        return 0

    monkeypatch.setattr("looma_agent.main.free_vram_bytes", считать)
    agent.config = type("C", (), {"heartbeat_interval_s": 0.05})()
    поток = threading.Thread(target=agent._vram_loop, daemon=True)
    поток.start()
    time.sleep(0.3)
    agent._stop.set()
    поток.join(1)
    assert сколько[0] == 0, "поверх идущего замера завёлся второй"


def test_пока_драйвер_молчит_видно_насколько_число_отстало(monkeypatch):
    """Оркестратору и панели нужно отличать «карта свободна» от «драйвер не
    отвечает уже десять минут»: во втором случае карта ещё числится в ресурсах,
    а задача на ней упадёт."""
    agent = _голый_агент()
    assert agent._vram_stale_s() == 0.0        # до первого удачного замера

    agent._vram_at = time.monotonic() - 300
    assert 299 < agent._vram_stale_s() < 302
