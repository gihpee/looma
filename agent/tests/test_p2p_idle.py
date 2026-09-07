"""Срок жизни соединения без данных.

Со стенда, замерено секундомером: туннель к голове кластера жил РОВНО 30
секунд, поток завершался штатно, без ошибки, и через три секунды кластер терял
узел. Трижды подряд с точностью до секунды — значит это не обрыв, а срок,
который мы не задавали и получали умолчанием чужой библиотеки.
"""

from __future__ import annotations

import pytest

from looma_agent.p2p import peer


class Строитель:
    """Записывает, что у него попросили, и возвращает себя."""

    def __init__(self, взято: dict) -> None:
        self.взято = взято

    def __getattr__(self, имя):
        def запомнить(*args, **kwargs):
            self.взято[имя] = args[0] if args else True
            return self
        return запомнить

    def build(self):
        return object()


def собрать(monkeypatch, tmp_path) -> dict:
    взято: dict = {}
    monkeypatch.setattr("lattica.Lattica.builder",
                        staticmethod(lambda: Строитель(взято)))
    node = peer.PeerNode(port=47100, key_dir=str(tmp_path))
    node._build_on(47100)
    return взято


def test_срок_бездействия_задан_явно(monkeypatch, tmp_path):
    """Соединение, на котором держится кластер, обязано жить столько же,
    сколько он сам. Умолчание чужой библиотеки для этого не годится."""
    pytest.importorskip("lattica")
    взято = собрать(monkeypatch, tmp_path)

    assert "with_idle_timeout" in взято, "срок бездействия не задан вовсе"
    # Не «достаточно большой», а «дольше любого кластера»: клиент платит за
    # всё время аренды и простаивать вправе сколько угодно.
    сутки = 24 * 3600
    assert взято["with_idle_timeout"] >= 7 * сутки, (
        f"срок {взято['with_idle_timeout']} с короче недели: арендованный "
        "кластер может простоять дольше, и связь оборвётся за бездействие")


def test_срок_настраивается_окружением(monkeypatch, tmp_path):
    """На узле с придирчивым роутером его может понадобиться уменьшить."""
    pytest.importorskip("lattica")
    monkeypatch.setenv("LOOMA_P2P_IDLE_TIMEOUT_S", "123")
    import importlib

    importlib.reload(peer)
    try:
        assert peer.IDLE_TIMEOUT_S == 123
    finally:
        monkeypatch.delenv("LOOMA_P2P_IDLE_TIMEOUT_S")
        importlib.reload(peer)
