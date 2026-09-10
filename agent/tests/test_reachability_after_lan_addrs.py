"""«Принимает входящие» не должно означать «показал хоть какой-то адрес».

Регрессия, замеченная в панели: после того как узел начал объявлять свои
локальные адреса (ради соседей за одним роутером), ВСЕ узлы разом стали
«принимает входящие» — включая те, до которых снаружи не дозвониться никак.

Причина: достижимость считалась как «есть адрес без /p2p-circuit», и
/ip4/10.124.10.12/tcp/47100 под это подходит.

Цена ошибки не в надписи. По этому же признаку агент решает, стоит ли ходить
к соседу напрямую, а оркестратор — связна ли группа: заведомо несвязная пара
проходила бы проверку.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from looma_agent.p2p.peer import dialable_from_outside


@pytest.mark.parametrize("addr", [
    "/ip4/10.124.10.12/tcp/47100",          # та самая пара за одним роутером
    "/ip4/192.168.0.56/udp/47100/quic-v1",
    "/ip4/172.17.0.1/tcp/47100",            # докеровский мост
    "/ip4/169.254.1.1/tcp/47100",           # link-local
    "/ip4/127.0.0.1/tcp/47100",
    "/ip6/::1/tcp/47100",
    "/ip6/fe80::1/tcp/47100",
])
def test_местный_адрес_не_считается_достижимостью(addr):
    assert dialable_from_outside(addr) is False


@pytest.mark.parametrize("addr", [
    "/ip4/95.79.46.1/tcp/47100",
    "/ip6/2a00:1450::1/tcp/47100",
    "/dns4/looma.example/tcp/47100/p2p/12D3KooWX",
])
def test_публичный_адрес_считается(addr):
    assert dialable_from_outside(addr) is True


def test_через_реле_не_считается():
    """Циркуитный адрес — это путь через реле под другим именем, а не
    способность принимать входящие."""
    assert dialable_from_outside(
        "/dns4/loomafloat.ru/tcp/47200/p2p/12D3KooWJ/p2p-circuit/p2p/12D3KooWM"
    ) is False


def test_узел_только_с_местными_адресами_недостижим():
    """Ровно карточка nv2 после правки: локальные адреса есть, публичных нет."""
    addrs = ["/ip4/10.124.10.12/tcp/47100",
             "/ip4/10.124.10.12/udp/47100/quic-v1"]
    assert not any(dialable_from_outside(a) for a in addrs)


def test_узел_с_публичным_адресом_достижим_несмотря_на_местные():
    """Локальные адреса не должны и мешать: они просто рядом."""
    addrs = ["/ip4/10.10.0.35/tcp/47100",
             "/ip4/95.79.46.1/tcp/47100",
             "/ip4/192.168.0.56/udp/47100/quic-v1"]
    assert any(dialable_from_outside(a) for a in addrs)
