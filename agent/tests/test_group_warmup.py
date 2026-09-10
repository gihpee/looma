"""Маршрут к соседу по группе наводится заранее, а не первым запросом.

Со стенда, nv2+nv3: nv2 доступен только через реле, и nv3 пятнадцать минут
получал на каждый туннель один и тот же отказ lattica — «Only relayed
connection available for peer». Соединение к узлу за NAT поднимается сначала
через реле и лишь потом пробивается в прямое; байтовый туннель поверх реле не
открывается вовсе, а Ray отводит на соединение пять секунд.

Конвейер наводит маршруты заранее (LinkTable.set_neighbours) с самого начала.
У Ray этого не было.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.control.tasks import TaskCommands
from looma_agent.tasks.groups import Group, Member


class Соседи:
    """Столько от PeerLayer, сколько трогает прогрев."""

    def __init__(self, *, отвечают=()) -> None:
        self.отвечают = set(отвечают)
        self.звали = []
        self._lock = threading.Lock()

    def warm(self, peer_id: str) -> bool:
        with self._lock:
            self.звали.append(peer_id)
        return peer_id in self.отвечают


def группа(rank: int = 0) -> Group:
    return Group(group_id="g1", rank=rank, members={
        0: Member(rank=0, node_id="nv2", peer_id="12D3KooWM7Bs-nv2"),
        1: Member(rank=1, node_id="nv3", peer_id="12D3KooWDee4-nv3"),
    })


def хранитель(peers) -> TaskCommands:
    keeper = TaskCommands.__new__(TaskCommands)
    keeper.peers = peers
    return keeper


def дождаться(условие, срок=3.0) -> bool:
    предел = time.monotonic() + срок
    while time.monotonic() < предел:
        if условие():
            return True
        time.sleep(0.02)
    return False


def test_маршрут_наводится_ко_всем_кроме_себя():
    peers = Соседи(отвечают={"12D3KooWDee4-nv3"})
    хранитель(peers)._warm_group(группа(rank=0))

    assert дождаться(lambda: peers.звали), "прогрев не начался"
    assert set(peers.звали) == {"12D3KooWDee4-nv3"}, "звали не тех"


def test_прогрев_не_держит_вызывающего():
    """Он идёт из обработки команды на запуск: заблокировать его — значит
    остановить обработку всех остальных команд узла."""
    class Долгий(Соседи):
        def warm(self, peer_id: str) -> bool:
            time.sleep(5)
            return True

    начали = time.monotonic()
    хранитель(Долгий())._warm_group(группа(rank=0))
    assert time.monotonic() - начали < 1.0, "прогрев выполнялся на месте"


def test_недостижимый_сосед_не_роняет_запуск(monkeypatch):
    """Сосед может ещё ставить окружение — сборка torch идёт минутами.
    Повторы для этого и нужны, но отказ не должен ничего ломать."""
    monkeypatch.setattr("looma_agent.control.tasks.WARM_ATTEMPTS", 2)
    monkeypatch.setattr("looma_agent.control.tasks.WARM_RETRY_S", 0.05)
    peers = Соседи(отвечают=set())          # не отвечает никто

    хранитель(peers)._warm_group(группа(rank=0))

    assert дождаться(lambda: len(peers.звали) >= 2), "повторов не было"


def test_без_p2p_прогревать_нечего():
    хранитель(None)._warm_group(группа(rank=0))     # не должно бросить
