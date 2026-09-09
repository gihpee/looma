"""Адреса рангов на петле там, где их нет по умолчанию.

Кластер собирается на адресах `127.0.0.<N+2>`. На Linux они существуют сразу —
там вся 127.0.0.0/8 поднята. На macOS назначен ровно один, `127.0.0.1`, и
остальные приходится заводить: без этого проброс отвечает задаче `HTTP 502`, а
сам Ray падает на «Can't assign requested address».
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.tasks import loopback


def test_первый_адрес_петли_есть_везде():
    """Иначе не работало бы вообще ничего, включая канал задачи."""
    assert loopback.available("127.0.0.1")


def test_на_linux_ничего_не_делаем(monkeypatch):
    """Там вся петля уже поднята, и трогать сетевые настройки узла не за чем."""
    monkeypatch.setattr(loopback.platform, "system", lambda: "Linux")
    вызовы = []
    monkeypatch.setattr(loopback.subprocess, "run",
                        lambda *a, **kw: вызовы.append(a))

    assert loopback.ensure("127.0.0.9")
    assert вызовы == [], "на Linux сеть узла не настраивается"


def test_за_пределы_петли_не_выходим(monkeypatch):
    """Поднять на lo0 адрес чужой сети — это уже не «наш узел», а вмешательство
    в машину, которую нам одолжили."""
    monkeypatch.setattr(loopback, "needed", lambda: True)
    monkeypatch.setattr(loopback, "available", lambda _a: False)
    вызовы = []
    monkeypatch.setattr(loopback.subprocess, "run",
                        lambda *a, **kw: вызовы.append(a))

    assert not loopback.ensure("10.0.0.5")
    assert вызовы == []


def test_существующий_адрес_не_записывается_в_свои(monkeypatch):
    """Уходя, мы снимаем только заведённое нами. Записать в свои чужое значит
    однажды отобрать у владельца адрес, который он поднял для себя."""
    monkeypatch.setattr(loopback, "needed", lambda: True)
    monkeypatch.setattr(loopback, "available", lambda _a: True)
    loopback._ours.clear()

    assert loopback.ensure("127.0.0.7")
    assert loopback._ours == set()


def test_поднятое_нами_снимается(monkeypatch):
    """Единственное, что агент менял в настройках машины, обязано вернуться
    как было."""
    monkeypatch.setattr(loopback, "needed", lambda: True)
    monkeypatch.setattr(loopback, "available", lambda _a: False)
    команды = []

    def записать(argv, **kwargs):
        команды.append(argv)

        class Ок:
            returncode = 0
        return Ок()

    monkeypatch.setattr(loopback.subprocess, "run", записать)
    loopback._ours.clear()

    assert loopback.ensure("127.0.0.8")
    loopback.release_all()

    assert команды[0][:3] == [loopback.IFCONFIG, loopback.INTERFACE, "alias"]
    assert команды[1][:3] == [loopback.IFCONFIG, loopback.INTERFACE, "-alias"]
    assert loopback._ours == set()


def test_отказ_не_роняет_агента(monkeypatch):
    """Без прав root адрес не завести. Это значит «кластер на нескольких
    машинах здесь не соберётся», а не «узел сломан»: инференс и одиночные
    задачи работают по-прежнему."""
    monkeypatch.setattr(loopback, "needed", lambda: True)
    monkeypatch.setattr(loopback, "available", lambda _a: False)

    def отказать(*_a, **_kw):
        raise OSError("нет прав")

    monkeypatch.setattr(loopback.subprocess, "run", отказать)

    assert not loopback.ensure("127.0.0.6")
