"""Снимок состояния для панели провайдера.

Файл, а не сокет: панель работает под вошедшим пользователем, агент — под
своим, и запускаются они в произвольном порядке. Файл переживает и это, и
перезапуск любой из сторон.
"""

from __future__ import annotations

import json
import pytest
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


# ------------------------------------------- доклад не останавливает узел
def _агент_для_доклада(interval: float = 0.2):
    """Агент без сети: нужны только поля, которыми живёт _telemetry_or_none."""
    import threading

    from looma_agent.main import Agent

    агент = Agent.__new__(Agent)
    агент._collecting = threading.Event()
    агент.config = type("Config", (), {"heartbeat_interval_s": interval})()
    агент._last_report = None
    агент._stuck_since = 0.0
    агент._said_stuck_at = 0.0
    return агент


def test_подвисший_доклад_не_останавливает_цикл():
    """Со стенда: снимок агента отстал на десять минут при живом процессе.
    Доклад собирается из нескольких источников, и один — состояние p2p — уходит
    в чужую библиотеку, где может задержаться неизвестно насколько. Ждать его
    дольше удара сердца нельзя: цикл встанет, узел перестанет отчитываться, а
    процесс будет выглядеть здоровым."""
    import threading

    агент = _агент_для_доклада()
    держим = threading.Event()
    агент._telemetry = lambda: держим.wait(30) or "поздно"

    начали = time.monotonic()
    агент._telemetry_or_none()
    assert time.monotonic() - начали < 1.0, "ждали вместе со сбором"
    держим.set()


def test_застрявший_сбор_не_превращается_в_вечное_молчание():
    """Главное из этой истории. Раньше на незавершившийся сбор возвращался
    None, и удар сердца не отправлял НИЧЕГО. Сбор, который не вернётся совсем,
    оставлял `_collecting` поднятым навсегда — и узел замолкал до конца жизни
    процесса. Со стенда, nv3: контейнер up, процесс жив, ядро и драйвер ни при
    чём, наверх не идёт ничего.

    Живой узел, о котором нечего сказать нового, — это всё ещё живой узел."""
    import threading

    агент = _агент_для_доклада()
    агент._telemetry = lambda: "доклад-1"
    assert агент._telemetry_or_none() == "доклад-1"

    # А теперь сбор навсегда завис.
    держим = threading.Event()
    агент._telemetry = lambda: держим.wait(30) or "поздно"
    for _ in range(5):
        assert агент._telemetry_or_none() == "доклад-1", (
            "узел замолчал вместо того, чтобы повторить прошлый доклад")
    держим.set()


def test_пока_ни_одного_доклада_не_было_говорить_нечего():
    """До первого удачного сбора повторять нечего, и выдумывать не надо."""
    агент = _агент_для_доклада()
    агент._collecting.set()
    assert агент._telemetry_or_none() is None


def test_второй_сбор_не_заводится_поверх_застрявшего():
    """Иначе на том же самом месте копились бы потоки — по одному на каждый
    удар сердца, и узел уходил бы в лимит по потокам."""
    агент = _агент_для_доклада(interval=5)
    агент._collecting.set()          # прошлый сбор ещё идёт
    агент._telemetry = lambda: pytest.fail("завели сбор поверх застрявшего")

    начали = time.monotonic()
    агент._telemetry_or_none()
    assert time.monotonic() - начали < 1.0, "ждали вместо того, чтобы пропустить"


def test_обычный_доклад_возвращается():
    import threading

    from looma_agent.main import Agent

    агент = Agent.__new__(Agent)
    агент._collecting = threading.Event()
    агент.config = type("Config", (), {"heartbeat_interval_s": 5})()
    агент._telemetry = lambda: "доклад"

    assert агент._telemetry_or_none() == "доклад"


def test_корень_узла_без_пробелов():
    """Со стенда: клиентский вход Ray не поднимался, потому что Ray собирает
    команду его запуска строкой и отдаёт в оболочку без кавычек —

        bash: /Library/Application: No such file or directory

    Так склеивает команды не он один, а по этому же пути лежат окружения задач,
    то есть чужой софт, про который мы заранее ничего не знаем. Пробел в корне
    ломает целый класс вещей, каждая из которых иначе выясняется по одной."""
    from looma_agent.config import default_root

    assert " " not in default_root(), (
        "путь установки снова с пробелом — Ray и подобные ему это не переживут")


def test_все_пути_узла_без_пробелов(tmp_path):
    """Не только корень: внутри него лежат окружения задач и веса, и путь до
    них уходит в командные строки чужого софта целиком."""
    from looma_agent.config import parse_args

    config = parse_args(["--key", "looma_x"])
    для_проверки = [config.root, config.tasks_dir, config.envs_dir,
                    config.models_dir, config.key_file, config.pause_file]

    плохие = [str(p) for p in для_проверки if " " in str(p)]
    assert not плохие, f"пробел в путях: {плохие}"
