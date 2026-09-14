"""Опрос состояния p2p не должен ходить в рантайм, пока идут туннели.

Со стенда, ДВАЖДЫ, на разных машинах и разных ОС (nv3 на Linux и MacBook),
py-spy показал одно и то же:

    Thread ... (active+gil): "looma-p2p-sampler"
        get_visible_maddrs (lattica/client.py:162)

`+gil` означает, что поток держит GIL — внутри вызова в lattica. Пока тот не
вернётся, в процессе не выполняется ни строчки Python: ни удар сердца, ни
разбор команд. Снаружи «узел жив, но молчит» — ровно то, что мы ловили четыре
раза, каждый раз находя другую невиновную причину.

Не возвращается он потому, что рантайм занят: кластер Ray держит десятки
туннелей, каждый — генератор на потоке рантайма. На простаивающем узле тот же
вызов отвечает за миллисекунды, поэтому и не воспроизводилось синтетикой.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.p2p.layer import PeerLayer


class Туннели:
    def __init__(self, сколько: int = 0) -> None:
        self.open_count = сколько


class Узел:
    """Столько от PeerNode, сколько трогает опрос."""

    def __init__(self, туннелей: int = 0) -> None:
        self.tunnels = Туннели(туннелей)
        self.спрошено = 0

    def visible_addrs(self):
        self.спрошено += 1
        return ["/ip4/10.0.0.1/tcp/47100"]

    def relay_rtt_ms(self):
        return 0.0

    def in_network(self):
        return True


def слой(узел) -> PeerLayer:
    layer = PeerLayer()
    layer.node = узел
    return layer


def test_без_туннелей_опрос_идёт():
    узел = Узел(туннелей=0)
    слой_ = слой(узел)

    assert слой_._busy() is False
    слой_._sample_once()
    assert узел.спрошено == 1


def test_с_туннелями_рантайм_не_трогаем():
    """Главное. Один такой вызов замораживает весь процесс."""
    узел = Узел(туннелей=22)
    слой_ = слой(узел)

    assert слой_._busy() is True


def test_цикл_опроса_пропускает_занятый_узел(monkeypatch):
    from looma_agent.p2p import layer as layer_mod

    monkeypatch.setattr(layer_mod, "SAMPLE_INTERVAL_S", 0.02)
    узел = Узел(туннелей=5)
    слой_ = слой(узел)
    слой_._start_sampler()
    time.sleep(0.2)
    слой_.node = None                      # остановить цикл
    time.sleep(0.1)

    assert узел.спрошено == 0, "опрос полез в рантайм при живых туннелях"


def test_когда_туннели_кончились_опрос_возобновляется(monkeypatch):
    from looma_agent.p2p import layer as layer_mod

    monkeypatch.setattr(layer_mod, "SAMPLE_INTERVAL_S", 0.02)
    узел = Узел(туннелей=3)
    слой_ = слой(узел)
    слой_._start_sampler()
    time.sleep(0.15)
    assert узел.спрошено == 0

    узел.tunnels.open_count = 0            # кластер сняли
    time.sleep(0.2)
    слой_.node = None
    time.sleep(0.05)

    assert узел.спрошено > 0, "опрос не вернулся после снятия кластера"


def test_узла_нет_значит_не_занят():
    слой_ = PeerLayer()
    слой_.node = None
    assert слой_._busy() is False


# ------------------------------------------------- занятость по ЗАДАЧАМ
def test_исходящие_туннели_тоже_считаются_занятостью():
    """Первая версия смотрела только на входящие туннели, и стенд показал,
    почему этого мало: в логе «убрал 93 разрешённых портов и 0 ВХОДЯЩИХ
    туннелей», а строкой выше форвардер закрыл 12 живых исходящих. Опрос решил,
    что свободно, пошёл в рантайм и заморозил процесс — третий дамп подряд
    с `+gil` на том же вызове."""
    узел = Узел(туннелей=0)                 # входящих нет
    слой_ = слой(узел)
    слой_.set_busy_probe(lambda: True)      # а задача есть

    assert слой_._busy() is True
    assert узел.спрошено == 0


def test_без_задач_опрос_идёт():
    узел = Узел(туннелей=0)
    слой_ = слой(узел)
    слой_.set_busy_probe(lambda: False)

    assert слой_._busy() is False
    слой_._sample_once()
    assert узел.спрошено == 1


def test_если_спросить_не_вышло_считаем_занятым():
    """Ошибиться в сторону «занят» стоит замерших чисел в панели. Ошибиться в
    другую — замолчавшего узла."""
    слой_ = слой(Узел(туннелей=0))
    слой_.set_busy_probe(lambda: (_ for _ in ()).throw(RuntimeError("нет")))

    assert слой_._busy() is True


def test_проба_агента_видит_и_взятые_задачи():
    """Между «взяли» и «пошла» лежит сборка окружения — минуты, в течение
    которых туннели уже могут открываться."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from looma_agent.main import Agent

    agent = Agent.__new__(Agent)
    agent.tasks = type("R", (), {"snapshot": lambda self: {"running": 0, "tasks": 1}})()
    assert agent._has_tasks() is True

    agent.tasks = type("R", (), {"snapshot": lambda self: {"running": 0, "tasks": 0}})()
    assert agent._has_tasks() is False

    agent.tasks = type("R", (), {
        "snapshot": lambda self: (_ for _ in ()).throw(RuntimeError("замок занят"))})()
    assert agent._has_tasks() is True
