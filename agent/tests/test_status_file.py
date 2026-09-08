"""Снимок состояния для панели провайдера.

Файл, а не сокет: панель работает под вошедшим пользователем, агент — под
своим, и запускаются они в произвольном порядке. Файл переживает и это, и
перезапуск любой из сторон.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent import status


def test_снимок_читается_как_json(tmp_path):
    status.write(tmp_path, {"running": True, "node_id": "nv3", "updated_at": 1.0})

    got = json.loads(status.path_for(tmp_path).read_text())
    assert got["node_id"] == "nv3"


def test_панель_никогда_не_видит_половину_записи(tmp_path):
    """Опрос идёт раз в две секунды и однажды попал бы ровно в середину.
    Поэтому пишется во временный файл и переименовывается: читающий видит либо
    прежнюю версию целиком, либо новую."""
    status.write(tmp_path, {"running": True, "tasks_running": 1})
    status.write(tmp_path, {"running": True, "tasks_running": 2})

    got = json.loads(status.path_for(tmp_path).read_text())
    assert got["tasks_running"] == 2
    # И ничего лишнего рядом не осталось: панель ищет по имени, а мусор в
    # каталоге агента живёт до конца дней.
    assert [p.name for p in tmp_path.iterdir()] == [status.NAME]


def test_панель_может_прочитать_то_что_написал_агент(tmp_path):
    """Агент — служебный демон, панель — обычный пользователь. Файл, который
    не читается никем кроме автора, не канал, а тишина."""
    status.write(tmp_path, {"running": True})

    assert status.path_for(tmp_path).stat().st_mode & 0o044


def test_невозможность_записать_не_роняет_агента(tmp_path):
    """Панель — удобство. Узел, вставший из-за файла для окна, — худший
    возможный размен."""
    закрыто = tmp_path / "нет-такого"

    status.write(закрыто, {"running": True})   # не должно бросить

    assert not status.path_for(закрыто).exists()


# ------------------------------------------------------- ключ из файла
def test_ключ_подхватывается_из_файла(tmp_path):
    """Установщик кладёт демон и уходит; ключ провайдер вводит потом, в панели.
    Без этого узел на пользовательской машине настроить было бы нечем."""
    from looma_agent.config import parse_args
    from looma_agent.main import _with_key_from_file

    config = parse_args(["--root", str(tmp_path)])
    config.key_file.write_text("looma_abc\n")

    assert _with_key_from_file(config, wait_s=0).join_key == "looma_abc"


def test_агент_ждёт_ключ_а_не_выходит_сразу(tmp_path):
    """launchd поднимает вышедший демон снова через несколько секунд. Агент,
    выходящий из-за отсутствия ключа, крутился бы так до вечера и засыпал
    журнал системы сообщениями, из которых ничего не следует."""
    import threading

    from looma_agent.config import parse_args
    from looma_agent.main import _with_key_from_file

    config = parse_args(["--root", str(tmp_path)])
    threading.Timer(0.3, lambda: config.key_file.write_text("looma_late")).start()

    assert _with_key_from_file(config, wait_s=10).join_key == "looma_late"


def test_ожидание_не_вечное(tmp_path):
    """Демон, который стоит молча и никогда не выходит, выглядит работающим —
    и launchd о нём ничего не скажет."""
    from looma_agent.config import parse_args
    from looma_agent.main import _with_key_from_file

    config = parse_args(["--root", str(tmp_path)])

    assert _with_key_from_file(config, wait_s=0).join_key == ""


# ------------------------------------------------------ остановка владельцем
def test_остановленный_узел_не_подключается(tmp_path):
    """Подключиться к оркестратору, чтобы тут же уйти, — значит показать узел в
    списке живых и не дать ему работы. Снаружи это неотличимо от поломки."""
    import threading

    from looma_agent.config import parse_args
    from looma_agent.main import _wait_while_paused

    config = parse_args(["--root", str(tmp_path)])
    config.pause_file.write_text("остановлено")
    threading.Timer(0.4, config.pause_file.unlink).start()

    начали = time.monotonic()
    _wait_while_paused(config, poll_s=0.1)

    assert time.monotonic() - начали >= 0.3, "не дождался разрешения"


def test_пока_узел_стоит_панель_видит_почему(tmp_path):
    """Иначе остановленный узел и мёртвый выглядят одинаково — а это первое,
    на что посмотрит владелец, когда решит, что что-то сломалось."""
    import threading

    from looma_agent.config import parse_args
    from looma_agent.main import _wait_while_paused

    config = parse_args(["--root", str(tmp_path)])
    config.pause_file.write_text("остановлено")
    threading.Timer(0.3, config.pause_file.unlink).start()
    _wait_while_paused(config, poll_s=0.1)

    снимок = json.loads(status.path_for(tmp_path).read_text())
    assert снимок["paused"] is True
    assert снимок["running"] is False
    assert "владельцем" in снимок["why"]


def test_без_файла_паузы_ничего_не_ждём(tmp_path):
    from looma_agent.config import parse_args
    from looma_agent.main import _wait_while_paused

    config = parse_args(["--root", str(tmp_path)])
    начали = time.monotonic()

    _wait_while_paused(config, poll_s=5.0)

    assert time.monotonic() - начали < 1.0
