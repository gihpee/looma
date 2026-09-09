"""Повторы при сборке кластера — без настоящего Ray.

Отдельно от test_cluster.py: тот запускает настоящий кластер и целиком закрыт
`importorskip("ray")`. Здесь проверяется логика повторов, которой Ray не нужен,
и закрывать её тем же условием значило бы не проверять её нигде: на машине без
Ray файл пропускается молча.
"""

from __future__ import annotations

import time

import pytest


def test_зависшая_попытка_считается_неудачной_и_повторяется(monkeypatch):
    """Так это и падало у живого кластера.

    Между машинами порт головы держит агент: он принимает соединение мгновенно,
    ещё до того, как на том конце появится GCS. «Соединение отвергнуто», на
    которое рассчитан быстрый провал, не приходит никогда — Ray просто ждёт.
    Раньше TimeoutExpired пролетал мимо повторов, и единственная попытка съедала
    весь срок.
    """
    import subprocess as sp

    from looma_ray import cluster

    monkeypatch.setattr(cluster, "JOIN_RETRY_S", 0.01)
    attempts = []

    class Result:
        returncode, stdout, stderr = 0, "", ""

    def hangs_then_works(*_a, **_k):
        attempts.append(1)
        if len(attempts) < 3:
            raise sp.TimeoutExpired(cmd=["ray", "start"], timeout=60)
        return Result()

    monkeypatch.setattr(sp, "run", hangs_then_works)
    cluster._run_start(["ray", "start"], rank=1, until=time.time() + 60, timeout_s=60)
    assert len(attempts) == 3, "зависание должно быть попыткой, а не концом"


def test_все_попытки_зависли_дают_внятный_отказ(monkeypatch):
    """Не стек вызовов из subprocess.py: по нему не понять, что произошло, и
    человек идёт разбираться не туда."""
    import subprocess as sp

    from looma_ray import cluster

    monkeypatch.setattr(cluster, "JOIN_RETRY_S", 0.01)

    def always_hangs(*_a, **_k):
        raise sp.TimeoutExpired(cmd=["ray", "start"], timeout=60)

    monkeypatch.setattr(sp, "run", always_hangs)
    with pytest.raises(cluster.ClusterRefused) as отказ:
        cluster._run_start(["ray", "start"], rank=1, until=time.time() + 1, timeout_s=60)
    assert "ранга 1" in str(отказ.value)
    assert "не ответил за 60с" in str(отказ.value)


def test_присоединяющийся_не_сдаётся_раньше_головы(monkeypatch):
    """Со стенда: поиск соседа в DHT занял четыре с половиной минуты.

    Прежде число попыток было задано жёстко (пять), и присоединяющийся сдавался
    примерно через 435 секунд, тогда как голова ждёт 900. Кластер собрался на
    четвёртой попытке — то есть едва разминулся с отказом по причине, которая к
    делу не относится. Теперь оба срока берутся из одной величины.
    """
    import subprocess as sp

    from looma_ray import cluster

    monkeypatch.setattr(cluster, "JOIN_RETRY_S", 0.01)
    попытки = []

    class Отказ:
        returncode, stdout, stderr = 1, "", "GCS не отвечает"

    monkeypatch.setattr(sp, "run", lambda *_a, **_k: попытки.append(1) or Отказ())
    with pytest.raises(cluster.ClusterRefused):
        cluster._run_start(["ray", "start"], rank=1, until=time.time() + 0.2)

    assert len(попытки) > 5, (
        f"должен пробовать, пока голова ждёт, а сделал {len(попытки)} попыток")


def test_срок_попытки_передаётся_в_subprocess(monkeypatch):
    """Иначе он остался бы украшением: значение есть, а на вызов не влияет."""
    import subprocess as sp

    from looma_ray import cluster

    видели = {}

    class Result:
        returncode, stdout, stderr = 0, "", ""

    def capture(*_a, **kw):
        видели.update(kw)
        return Result()

    monkeypatch.setattr(sp, "run", capture)
    cluster._run_start(["ray", "start"], rank=1, until=0.0, timeout_s=42)
    assert видели.get("timeout") == 42


def test_зависший_ray_доносит_свои_слова(monkeypatch):
    """Единственный след того, чем он был занят.

    Вывод не льётся наружу, пока процесс жив, а живёт он до самого таймаута.
    Первая версия этой ветки выбрасывала вывод и подставляла догадку о причине
    («порт головы открыт») — догадка оказалась ещё и неверной: порт был закрыт,
    соединение отвергалось. Человек остался без единого следа.
    """
    import subprocess as sp

    from looma_ray import cluster

    monkeypatch.setattr(cluster, "JOIN_RETRY_S", 0.01)

    def hangs(*_a, **_k):
        raise sp.TimeoutExpired(
            cmd=["ray", "start"], timeout=60,
            output="подняли дашборд\n", stderr="RuntimeError: version mismatch\n")

    monkeypatch.setattr(sp, "run", hangs)
    with pytest.raises(cluster.ClusterRefused) as отказ:
        cluster._run_start(["ray", "start"], rank=0, until=0.0, timeout_s=60)
    сказано = str(отказ.value)
    assert "version mismatch" in сказано, "слова ray обязаны дойти"
    assert "подняли дашборд" in сказано


def test_молчаливое_зависание_так_и_называется(monkeypatch):
    """Пустой вывод — тоже сведение, и врать про него нельзя."""
    import subprocess as sp

    from looma_ray import cluster

    def hangs(*_a, **_k):
        raise sp.TimeoutExpired(cmd=["ray", "start"], timeout=60)

    monkeypatch.setattr(sp, "run", hangs)
    with pytest.raises(cluster.ClusterRefused) as отказ:
        cluster._run_start(["ray", "start"], rank=0, until=0.0, timeout_s=60)
    assert "и ничего не сказал" in str(отказ.value)


def test_байтовый_вывод_тоже_доносится(monkeypatch):
    """text=True не гарантирован для полей TimeoutExpired во всех версиях."""
    import subprocess as sp

    from looma_ray import cluster

    def hangs(*_a, **_k):
        raise sp.TimeoutExpired(cmd=["ray", "start"], timeout=60,
                                stderr=b"GCS \xd0\xbd\xd0\xb5 \xd0\xb2\xd1\x81\xd1\x82\xd0\xb0\xd0\xbb\n")

    monkeypatch.setattr(sp, "run", hangs)
    with pytest.raises(cluster.ClusterRefused) as отказ:
        cluster._run_start(["ray", "start"], rank=0, until=0.0, timeout_s=60)
    assert "GCS не встал" in str(отказ.value)


def test_ранг_поднимается_на_своём_адресе_а_не_на_локалхосте():
    """Ровно то, обо что кластер разваливался последним.

    Ray записывает узел в кластер под адресом, который выбрал сам, и голова
    потом по нему проверяет живость. `127.0.0.1` он под это не берёт — подменяет
    адресом машины: со стенда голова записалась как 192.168.2.84, а второй узел
    как 10.124.10.11, каждый адресом СВОЕЙ локальной сети. Пять неудачных
    проверок — и узел объявлен мёртвым.
    """
    from looma_ray.cluster import _common_flags
    from looma_ray.ports import ports_for

    for rank in (0, 1, 2):
        flags = _common_flags(ports_for(rank), None, rank)
        адрес = flags[flags.index("--node-ip-address") + 1]
        assert адрес != "127.0.0.1"
        assert адрес == f"127.0.0.{2 + rank}"


def test_на_macos_снимается_запрет_на_многоузловой_кластер(monkeypatch):
    """Ray отказывается собирать такой кластер под macOS и говорит это прямо:

        PANIC -- Multi-node Ray clusters are not supported on Windows and OSX.
        Restart the Ray cluster with the environment variable
        RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER=1

    Запрет предупредительный: сам Ray тут же называет переменную, которая его
    снимает. Со стенда — семь попыток подряд с этим текстом, при том что
    проброс портов уже работал."""
    import os
    import platform

    from looma_ray.cluster import allow_cluster_here

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.delenv("RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER", raising=False)

    allow_cluster_here()

    assert os.environ["RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER"] == "1"


def test_слово_оператора_сильнее(monkeypatch):
    """Он мог выставить ноль нарочно — чтобы увидеть этот отказ, а не то
    неизвестное, во что упрётся кластер дальше."""
    import os
    import platform

    from looma_ray.cluster import allow_cluster_here

    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setenv("RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER", "0")

    allow_cluster_here()

    assert os.environ["RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER"] == "0"


def test_на_linux_ничего_не_трогаем(monkeypatch):
    """Там запрета нет, и переменная только сбивала бы с толку того, кто её
    однажды увидит в окружении задачи."""
    import os
    import platform

    from looma_ray.cluster import allow_cluster_here

    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.delenv("RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER", raising=False)

    allow_cluster_here()

    assert "RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER" not in os.environ


def test_вход_снаружи_проверяется_отдельно_от_кластера(monkeypatch, tmp_path):
    """Со стенда, дважды: кластер собран, оба узла в нём, всё живо — а
    `looma-connect` получает «connection refused». Читается это как поломка
    сети между машинами, хотя сеть ни при чём: не поднялся клиентский вход."""
    from looma_ray import cluster

    monkeypatch.setattr(cluster, "_reachable", lambda *a, **kw: False)
    monkeypatch.setenv("RAY_TMPDIR", str(tmp_path))

    беда = cluster.client_entry_ready(31807, wait_s=0.2)

    assert "31807" in беда
    assert "кластер" in беда.lower(), "молчит о том, что сам кластер при этом жив"


def test_без_ray_client_говорится_прямо(monkeypatch):
    """Порт ноль означает, что вход не поднимали вовсе: в установке нет
    ray[client]. Это не поломка, но подключиться снаружи будет нечем."""
    from looma_ray import cluster

    беда = cluster.client_entry_ready(0)

    assert "ray[client]" in беда


def test_слова_клиентского_сервера_попадают_в_отказ(monkeypatch, tmp_path):
    """`ray start` про его неудачу молчит и код возврата не меняет — Ray пишет
    её только в свой лог. Без этих строк «вход не принимает» остаётся тупиком."""
    from looma_ray import cluster

    logs = tmp_path / "session_2026" / "logs"
    logs.mkdir(parents=True)
    (logs / "ray_client_server.err").write_text("Traceback\nAddress already in use\n")
    monkeypatch.setattr(cluster, "_reachable", lambda *a, **kw: False)
    monkeypatch.setenv("RAY_TMPDIR", str(tmp_path))

    беда = cluster.client_entry_ready(31807, wait_s=0.2)

    assert "Address already in use" in беда


def test_вход_принимает_молча(monkeypatch):
    """Хорошая новость не должна занимать место в логе задачи наравне с плохой:
    отсутствие отказа и есть отсутствие проблемы."""
    from looma_ray import cluster

    monkeypatch.setattr(cluster, "_reachable", lambda *a, **kw: True)

    assert cluster.client_entry_ready(31807, wait_s=5.0) == ""


def test_уборка_не_трогает_посторонних(monkeypatch):
    """Проверено на стенде и стоило испуга: путь сессии стоит в командной
    строке у кого угодно, кто про неё говорит, — у агента, который запустил
    задачу, и даже у оболочки, где набрали команду. Первая версия уборки нашла
    такой процесс и сняла его.
    """
    import os

    from looma_ray import cluster

    снятые = []

    class Процесс:
        def __init__(self, pid, name, cmdline):
            self.pid = pid
            self.info = {"pid": pid, "name": name, "cmdline": cmdline}

        def terminate(self):
            снятые.append(self.info["name"])

    посторонние = [
        Процесс(1, "zsh", ["zsh", "-c", "echo /tmp/session-1"]),
        Процесс(2, "python", ["python", "-m", "looma_agent.main", "--root", "/tmp/session-1"]),
    ]
    рэй = Процесс(3, "raylet", ["raylet", "--session_dir=/tmp/session-1"])

    monkeypatch.setattr(cluster, "os", os)
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda _f: [*посторонние, рэй])
    monkeypatch.setattr(psutil, "wait_procs", lambda p, timeout=0: (p, []))

    cluster.stop_node("/tmp/session-1")

    assert снятые == ["raylet"], f"сняли лишнее: {снятые}"


def test_уборка_снимает_демоны_ray(monkeypatch):
    """Ray заводит их в отдельной сессии, и SIGTERM группе задачи до них не
    доходит. Пережившие уборку gcs и raylet держат порты своего окна, и
    следующий кластер с тем же окном встать уже не может — снаружи это
    выглядит как «оба узла running, а рангов нет»."""
    from looma_ray import cluster

    снятые = []

    class Процесс:
        def __init__(self, pid, name):
            self.pid = pid
            self.info = {"pid": pid, "name": name,
                         "cmdline": [name, "--temp-dir=/tmp/session-9"]}

        def terminate(self):
            снятые.append(self.info["name"])

    демоны = [Процесс(11, "raylet"), Процесс(12, "gcs_server"),
              Процесс(13, "plasma_store")]
    import psutil
    monkeypatch.setattr(psutil, "process_iter", lambda _f: list(демоны))
    monkeypatch.setattr(psutil, "wait_procs", lambda p, timeout=0: (p, []))

    cluster.stop_node("/tmp/session-9")

    assert sorted(снятые) == ["gcs_server", "plasma_store", "raylet"]
