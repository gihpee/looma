"""Соседи по локальной сети должны находиться напрямую.

Со стенда, nv2+nv3: две машины одного владельца за одним роутером —
10.124.10.11 и 10.124.10.12, наружу обе через 95.79.46.1. Пробивание дырок
целится в общий публичный адрес, роутер не разворачивает пакет обратно внутрь
себя, прямого соединения не возникает. Байтовый туннель Ray поверх реле не
открывается, и кластер не собирается вовсе — при том, что машины стоят в
метре друг от друга и видят друг друга по 10.124.10.x.

С машинами в РАЗНЫХ сетях этого не было, и оттого поломка выглядела как
случайная: карточки узлов в панели одинаковые, NAT у всех не симметричный.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.p2p import peer


def test_локальные_адреса_объявляются(monkeypatch):
    monkeypatch.setattr(peer, "_local_ips", lambda: ["10.124.10.12"])

    addrs = peer._lan_addrs(47100)

    assert "/ip4/10.124.10.12/tcp/47100" in addrs
    assert "/ip4/10.124.10.12/udp/47100/quic-v1" in addrs


def test_публичный_адрес_не_объявляется_как_локальный(monkeypatch):
    """Свой публичный адрес узел объявит сам через AutoNAT. А объявить чужой
    NAT-адрес значит позвать соседей туда, где их никто не ждёт."""
    monkeypatch.setattr(peer, "_local_ips", lambda: ["95.79.46.1"])

    assert peer._lan_addrs(47100) == []


def test_петля_не_объявляется(monkeypatch):
    monkeypatch.setattr(peer, "_local_ips", lambda: ["127.0.0.1"])

    assert peer._lan_addrs(47100) == []


def test_несколько_интерфейсов_объявляются_все(monkeypatch):
    """У машины с докером их десяток, и заранее не известно, по какому из них
    сосед окажется достижим."""
    monkeypatch.setattr(peer, "_local_ips", lambda: ["10.124.10.12", "172.17.0.1"])

    addrs = peer._lan_addrs(47100)

    assert "/ip4/10.124.10.12/tcp/47100" in addrs
    assert "/ip4/172.17.0.1/tcp/47100" in addrs


def test_широковещательный_поиск_выключен_по_умолчанию(monkeypatch):
    """Он сыплет в лог по строке ERROR на каждый интерфейс без маршрута, а у
    машин с докером их десяток. Штатный путь — объявление локальных адресов."""
    monkeypatch.delenv("LOOMA_P2P_MDNS", raising=False)
    assert peer._mdns_enabled() is False


def test_широковещательный_поиск_можно_включить(monkeypatch):
    """Запасной путь на случай, если объявления адресов окажется мало."""
    for значение in ("1", "true", "yes"):
        monkeypatch.setenv("LOOMA_P2P_MDNS", значение)
        assert peer._mdns_enabled() is True
