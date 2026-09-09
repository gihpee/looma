"""Поднять узел Ray и дождаться, пока кластер соберётся.

`ray start` подпроцессом, а не `ray.init()` из этого процесса: узел кластера
должен пережить наш скрипт, а сам процесс — остаться свободным, чтобы отвечать
на /health, пока Ray поднимается. Это те же минуты, что у стадии уходят на
веса, и всё это время снаружи надо видеть разницу между «поднимается» и
«отвечает».
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional

from looma_ray.ports import (RankPorts, group_base, head_address, loopback_for,
                             ports_for)

logger = logging.getLogger("looma_ray.cluster")

# Сколько ранг ждёт голову. Голова поднимается быстро, но её задача может
# стоять в очереди за окружением, а окружение — это pip.
HEAD_WAIT_S = float(os.environ.get("LOOMA_RAY_HEAD_WAIT_S", "900"))
# Сколько голова ждёт остальных, прежде чем считать это провалом.
JOIN_WAIT_S = float(os.environ.get("LOOMA_RAY_JOIN_WAIT_S", "900"))
# Сколько ждёт между попытками присоединиться. Число попыток не задаётся: они
# идут, пока голова ждёт остальных (JOIN_WAIT_S). Раньше их было пять, то есть
# около 435 секунд против её 900 — присоединяющийся сдавался вдвое раньше, чем
# голова переставала его ждать. Со стенда: поиск соседа в DHT занял ЧЕТЫРЕ с
# половиной минуты, и кластер собрался на четвёртой попытке из шести — то есть
# едва разминулся с отказом по причине, к делу не относящейся.
JOIN_RETRY_S = float(os.environ.get("LOOMA_RAY_JOIN_RETRY_S", "15"))
# Сколько ждать ОДНУ попытку `ray start`. Отдельно от бюджета всех попыток:
# раньше здесь стояло 600 секунд при бюджете примерно в 75 — одна попытка
# съедала весь срок, и повтора не случалось ни разу.
START_TIMEOUT_S = float(os.environ.get("LOOMA_RAY_START_TIMEOUT_S", "60"))
# Голове срок длиннее: повторов у неё нет, ждать ей некого, а подняться на
# занятой машине она может и не за минуту.
HEAD_START_TIMEOUT_S = float(os.environ.get("LOOMA_RAY_HEAD_START_TIMEOUT_S", "300"))


# По чему узнаётся процесс Ray. Список, а не догадка по имени питона: Ray
# запускает и свои двоичные файлы (raylet, gcs_server), и питоновские модули, и
# воркеры с переименованным процессом.
RAY_PROCESSES = ("raylet", "gcs_server", "plasma_store", "ray::",
                 "ray/dashboard", "log_monitor", "ray.scripts",
                 "ray/_private", "runtime_env_agent")


# Взводится, когда задачу снимают. Ожидания смотрят на него, иначе SIGTERM во
# время сборки не прерывал бы её, а просто убивал процесс — и `ray stop` не
# успевал бы отработать, оставляя чужой машине работающий кластер.
STOP = threading.Event()


class ClusterRefused(RuntimeError):
    """Кластер не собрался, и вот почему."""


class Stopped(ClusterRefused):
    """Сборку прервали снаружи. Не отказ — решение."""


def _own_address() -> str:
    """Адрес этой машины в её сети. Часть служб Ray биндится на него, а не на
    петлю, и тогда проверка по одному локалхосту говорит «не принимает» о
    работающем сервере."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 53))
        found = probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()
    return "" if not found or found.startswith("127.") else found


def _reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_head(base: int = 0, stride: int = 0, *, timeout_s: float = 0.0) -> str:
    """Дождаться, пока голова начнёт принимать соединения.

    Проверяется соединением, а не файлом и не паузой: между «ranks стартовали
    одновременно» и «голова готова» лежит установка окружения на её узле, и
    сколько она займёт, отсюда знать нельзя.
    """
    address = head_address(base, stride)
    host, port = address.split(":")
    deadline = time.time() + (timeout_s or HEAD_WAIT_S)
    while time.time() < deadline:
        if STOP.is_set():
            raise Stopped("сборку кластера прервали")
        if _reachable(host, int(port)):
            return address
        STOP.wait(1.0)
    raise ClusterRefused(
        f"голова кластера не отозвалась на {address} за "
        f"{timeout_s or HEAD_WAIT_S:.0f}с. Если ранги на разных узлах, это "
        "означает, что между ними нет проброса портов (docs/RAY.md)")


def _common_flags(ports: RankPorts, gpus: Optional[int], rank: int) -> List[str]:
    flags = [
        # Свой адрес на петле, а не 127.0.0.1: последний Ray под узел не берёт,
        # а подменяет адресом машины — и узел записывается в кластер под
        # адресом своей локальной сети, до которого с чужой машины не дойти.
        # См. ports.loopback_for.
        "--node-ip-address", loopback_for(rank),
        "--node-manager-port", str(ports.node_manager),
        "--object-manager-port", str(ports.object_manager),
        "--runtime-env-agent-port", str(ports.runtime_env_agent),
        "--dashboard-agent-listen-port", str(ports.dashboard_listen),
        "--dashboard-agent-grpc-port", str(ports.dashboard_grpc),
        "--metrics-export-port", str(ports.metrics),
        "--min-worker-port", str(ports.worker_first),
        "--max-worker-port", str(ports.worker_last),
        # Узел чужой: молча слать телеметрию с него наружу мы не будем.
        "--disable-usage-stats",
    ]
    if gpus is not None:
        flags += ["--num-gpus", str(gpus)]
    # Сколько воркеров поднимать. Без этого Ray считает своими ВСЕ ядра машины
    # и пред-запускает воркер на каждое — а ядра на узле делятся, и два ранга
    # на одной машине заводят вдвое больше процессов, чем она стоит. Каждый со
    # своими потоками, и упирается это в лимит задолго до пользы.
    cpus = _own_cpus()
    if cpus:
        flags += ["--num-cpus", str(cpus)]
    return flags


def _own_cpus() -> int:
    """Своя доля процессора, как её назвал агент. Ноль — «решай сам»."""
    try:
        share = float(os.environ.get("LOOMA_TASK_CPUS", "") or 0)
    except ValueError:
        return 0
    return max(1, int(share)) if share > 0 else 0


def allow_cluster_here() -> None:
    """Снять запрет Ray на многоузловой кластер под macOS.

    Ray отказывается собирать такой кластер и говорит это прямо:

        PANIC -- Multi-node Ray clusters are not supported on Windows and OSX.
        Restart the Ray cluster with the environment variable
        RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER=1

    Запрет предупредительный, а не следствие отсутствующей возможности: сам Ray
    тут же называет переменную, которая его снимает. Мы её и ставим — на узле,
    который согласился отдать машину под кластер, отказ ради осторожности
    решает не за нас.

    Только на macOS и только если оператор не сказал своего: он мог выставить
    ноль нарочно, чтобы увидеть этот отказ вместо неизвестно чего дальше.
    """
    if platform.system() != "Darwin":
        return
    if os.environ.get("RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER") is None:
        os.environ["RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER"] = "1"
        logger.info("macOS: включаю многоузловой режим Ray, который он держит "
                    "выключенным по умолчанию (RAY_ENABLE_WINDOWS_OR_OSX_CLUSTER=1)")


def start_node(rank: int, size: int, *, gpus: Optional[int] = None,
               base: int = 0, stride: int = 0, temp_dir: str = "") -> str:
    """Поднять узел Ray для этого ранга. Возвращает адрес головы."""
    # До запуска: подпроцесс наследует окружение этого процесса, и переменная,
    # поставленная позже, до `ray start` уже не доедет.
    allow_cluster_here()
    # Окно группы, а не общее для всех: брошенный кластер занимает СВОИ порты,
    # и новый его больше не встречает.
    base = base or group_base(size, stride=stride)
    ports = ports_for(rank, base=base, stride=stride)
    argv = [sys.executable, "-m", "ray.scripts.scripts", "start"]
    if rank == 0:
        # --include-dashboard только здесь: Ray отвергает его у неголовных
        # рангов целиком, а не игнорирует. Смотреть на дашборд всё равно
        # неоткуда — у узла нет входящих портов.
        argv += ["--head", "--port", str(ports.gcs),
                 "--include-dashboard", "false"]
        # Клиентский вход — только если установлен ray[client]. Флаг без него
        # не игнорируется, а роняет `ray start` целиком, так что спрашиваем
        # заранее и молча обходимся без него: кластер полезен и так, просто
        # подключиться снаружи будет нечем.
        if client_server_available():
            argv += ["--ray-client-server-port", str(ports.client_server)]
            logger.info("клиентский вход будет на %s:%d",
                        loopback_for(0), ports.client_server)
        else:
            logger.info("ray[client] не установлен: внешнего входа у кластера "
                        "не будет (добавьте ray[client] в требования)")
        address = head_address(base, stride)
    else:
        address = wait_for_head(base, stride)
        argv += ["--address", address]
    argv += _common_flags(ports, gpus, rank)
    if temp_dir:
        argv += ["--temp-dir", temp_dir]

    if rank == 0 and _occupied(loopback_for(0), ports.gcs):
        # Иначе Ray подключится к чужому кластеру и упадёт на несовпадении
        # имени сессии — сообщении, из которого причина не следует вовсе, и
        # искать её пойдут в своём коде, а не в списке процессов.
        raise ClusterRefused(
            f"порт {ports.gcs} уже занят: похоже, там живёт кластер прошлой "
            "попытки. Снимите старую группу — или, если это чужой процесс, "
            "сдвиньте окно через LOOMA_RAY_PORT_BASE")

    # «Пробую», а не «подключаюсь»: строка пишется ДО запуска. Прежняя
    # формулировка обещала состоявшееся подключение, и по ней невозможно было
    # отличить успех от зависания — в логе они выглядели одинаково.
    logger.info("ранг %d/%d: %s", rank, size,
                "поднимаю голову" if rank == 0 else f"пробую подключиться к {address}")
    _run_start(argv, rank=rank,
               # Голове повторять незачем: ждать ей некого. Остальные пробуют,
               # пока она их ждёт, — сроки берутся из одной величины и потому
               # не могут разойтись.
               until=0.0 if rank == 0 else time.time() + JOIN_WAIT_S,
               timeout_s=HEAD_START_TIMEOUT_S if rank == 0 else START_TIMEOUT_S)
    return address


def client_server_available() -> bool:
    """Есть ли в этой установке серверная часть Ray Client.

    Проверяем импортом, а не версией: `--ray-client-server-port` при её
    отсутствии не игнорируется, а роняет `ray start` — и падает это сообщением
    про аргумент, из которого не следует, что не хватает пакета.
    """
    from importlib.util import find_spec

    try:
        return find_spec("ray.util.client.server.proxier") is not None
    except (ImportError, ValueError):
        return False


def ray_version() -> str:
    """Версия Ray, которой поднят кластер. Клиенту нужна такая же."""
    try:
        import ray

        return str(ray.__version__)
    except Exception:
        return ""


def client_entry_ready(port: int, *, wait_s: float = 30.0) -> str:
    """Принимает ли клиентский вход. Пусто — принимает; иначе почему нет.

    Проверяется отдельно от сборки кластера, потому что ломается отдельно:
    кластер собирается, узлы в нём, всё живо — а `looma-connect` получает
    «connection refused» и выглядит это поломкой сети между машинами. Со стенда
    ровно так и было, дважды.

    С ожиданием: клиентский сервер поднимается не в ту же секунду, что и голова,
    и проверка сразу после `ray start` застаёт его на полпути.
    """
    if not port:
        return ("клиентский вход не поднимался: в этой установке нет ray[client]. "
                "Кластер работает, но подключиться снаружи нечем")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if STOP.is_set():
            return ""
        for host in (loopback_for(0), "127.0.0.1", _own_address()):
            if not host:
                continue
            if _reachable(host, port, timeout=1.0):
                logger.info("клиентский вход принимает на %s:%d", host, port)
                return ""
        STOP.wait(1.0)
    return (f"клиентский вход Ray не принимает на порту {port} ни на "
            f"{loopback_for(0)}, ни на 127.0.0.1 за {wait_s:.0f}с. Кластер при "
            "этом собран и работает — не работать будет только подключение "
            "снаружи (looma-connect)." + client_server_said())


def client_server_said(temp_dir: str = "") -> str:
    """Что Ray написал в лог своего клиентского сервера.

    Он пишет туда, и только туда: `ray start` про его неудачу молчит и код
    возврата не меняет. Без этих строк «вход не принимает» — тупик, а с ними
    видно, на чём именно он не поднялся.
    """
    base = Path(temp_dir or os.environ.get("RAY_TMPDIR")
                or os.environ.get("LOOMA_TASK_TMP") or "/tmp")
    try:
        logs = sorted(base.glob("session_*/logs/ray_client_server*"))
    except OSError:
        return ""
    if not logs:
        return (f" Лога клиентского сервера в {base} нет вовсе — похоже, Ray "
                "его и не запускал")
    сказано = []
    for path in logs[-2:]:
        try:
            строки = [s.strip() for s in path.read_text(
                errors="replace").splitlines() if s.strip()]
        except OSError:
            continue
        if строки:
            сказано.append(f"{path.name}: " + " / ".join(строки[-5:]))
    return (" Клиентский сервер сказал: " + "; ".join(сказано)) if сказано else ""


def client_port(size: int, *, base: int = 0, stride: int = 0) -> int:
    """Порт, на котором кластер принимает клиентов. Ноль — не принимает."""
    if not client_server_available():
        return 0
    base = base or group_base(size, stride=stride)
    return ports_for(0, base=base, stride=stride).client_server


def _occupied(host: str, port: int) -> bool:
    """Слушает ли кто-то этот адрес прямо сейчас.

    Адрес, а не только порт: у каждого ранга он свой, и занятость на чужом
    ничего про наш не говорит.
    """
    with socket.socket() as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


def _said(stdout, stderr) -> str:
    """Последнее, что сказал `ray start`. Годится и для завершившегося, и для
    снятого по таймауту: у TimeoutExpired те же поля, просто заполнены не до
    конца."""
    def текст(value) -> str:
        if not value:
            return ""
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else value

    said = (текст(stderr) + "\n" + текст(stdout)).strip().splitlines()
    said = [line for line in said if line.strip()]
    return " / ".join(said[-4:]) if said else "и ничего не сказал"


def _run_start(argv: List[str], *, until: float = 0.0,
               rank: int = 0, timeout_s: float = START_TIMEOUT_S) -> None:
    """Запустить `ray start`, повторяя, пока голова не примет.

    Повтор нужен именно неголовным рангам. Открытый порт головы не значит, что
    голова готова: GCS занимает его в первую секунду, а собирается ещё
    десятки — и присоединение в этом промежутке падает по таймауту raylet'а.
    Снаружи «занимает порт» и «готова» неотличимы, поэтому вместо угадывания
    здесь просто пробуют ещё раз.

    Зависание — такая же неудачная попытка, как ненулевой код возврата, и
    считается ею. Раньше `subprocess.run(timeout=...)` бросал TimeoutExpired
    мимо всего этого: исключение пролетало и проверку кода, и повторы, и
    осмысленный отказ, а наружу уходил стек вызовов из subprocess.py.

    Причём зависание здесь — не редкий случай, а основной. Между машинами порт
    головы держит агент, подставляя туда туннель. Такой слушатель принимает
    соединение мгновенно, ещё до того, как на том конце что-то появится, и
    «соединение отвергнуто» — сигнал, на котором построен быстрый провал, — не
    приходит никогда. Ray ждёт ответа, которого нет, пока его не снимут.

    Убирать за оборванной попыткой здесь нечем: `ray stop` снял бы и соседние
    ранги того же пользователя (см. stop_node). Если попытка успела занять
    порт, следующая честно скажет об этом своим сообщением.
    """
    last = ""
    attempt = 0
    while True:
        attempt += 1
        if STOP.is_set():
            raise Stopped("сборку кластера прервали")
        try:
            result = subprocess.run(argv, capture_output=True, text=True,
                                    timeout=timeout_s)
        except subprocess.TimeoutExpired as slow:
            # То, что он успел сказать, — единственное, что вообще известно о
            # зависании. Выбрасывать это и подставлять свою догадку о причине
            # (так было в первой версии) значит оставить человека без всякого
            # следа: вывод не льётся наружу, пока процесс жив, а он живёт до
            # самого таймаута.
            last = f"не ответил за {timeout_s:.0f}с; {_said(slow.stdout, slow.stderr)}"
        else:
            if result.returncode == 0:
                return
            last = _said(result.stdout, result.stderr)
        if time.time() + JOIN_RETRY_S >= until:
            break
        logger.warning("ранг %d: голова ещё не принимает (попытка %d, осталось %.0f с): %s",
                       rank, attempt, until - time.time(), last[:200])
        STOP.wait(JOIN_RETRY_S)
    raise ClusterRefused(f"ray start для ранга {rank} не отработал: {last}")


def wait_for_group(size: int, *, timeout_s: float = 0.0) -> int:
    """Дождаться, пока в кластере окажутся ВСЕ ранги.

    Не «голова поднялась»: клиентский код, запущенный на половине кластера,
    не падает — он считает вдвое дольше и молча. Разницу видно только отсюда.
    """
    import ray

    deadline = time.time() + (timeout_s or JOIN_WAIT_S)
    seen = 0
    while time.time() < deadline:
        if STOP.is_set():
            raise Stopped("ожидание рангов прервали")
        seen = alive_nodes()
        if seen >= size:
            return seen
        STOP.wait(1.0)
    raise ClusterRefused(
        f"в кластере {seen} узлов из {size} — остальные не подключились за "
        f"{timeout_s or JOIN_WAIT_S:.0f}с")


# Подключение к Ray делается один раз и переиспользуется. Раньше на каждый
# опрос заводился поток, а опрос идёт раз в секунду до пятнадцати минут — и
# это ровно то, что добивало узел, уже занятый чужими процессами.
_CONNECTED = threading.Event()
_BROKEN = threading.Event()


def _connect(timeout_s: float) -> bool:
    """Подключиться к своему же узлу Ray. Один раз за жизнь процесса.

    С потолком по времени: `ray.init` своего таймаута не имеет и против
    умершей головы висит молча. Один такой вызов подвешивал весь ранг — он
    оставался «running» и не отвечал ничего, что хуже любой ошибки.
    """
    if _CONNECTED.is_set():
        return True
    if _BROKEN.is_set():
        return False

    def dial() -> None:
        try:
            import ray

            if not ray.is_initialized():
                ray.init(address="auto", log_to_driver=False,
                         ignore_reinit_error=True, configure_logging=False)
            _CONNECTED.set()
        except Exception as exc:
            logger.warning("к Ray не подключиться: %s", exc)
            _BROKEN.set()

    worker = threading.Thread(target=dial, name="ray-connect", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if not _CONNECTED.is_set() and not _BROKEN.is_set():
        logger.warning("Ray не ответил за %.0fс; больше не ждём", timeout_s)
        _BROKEN.set()
    return _CONNECTED.is_set()


def alive_nodes(timeout_s: float = 30.0) -> int:
    """Сколько узлов сейчас живо. Ноль означает и «нет узлов», и «Ray ещё не
    поднялся» — для /health разница неважна, оба значат «не готов»."""
    if not _connect(timeout_s):
        return 0
    try:
        import ray

        return sum(1 for node in ray.nodes() if node.get("Alive"))
    except Exception:
        return 0


def registered_addresses() -> str:
    """Под какими адресами узлы записаны в кластере — словами самого Ray.

    Не то же, что мы передали флагом. Ray подменяет `127.0.0.1` на адрес
    машины, и именно записанный адрес голова использует, когда проверяет
    живость узла и когда раздаёт ему работу. Если он локальный для чужой сети,
    голова стучится в пустоту: со стенда — пять неудачных проверок подряд, узел
    помечен мёртвым, и ни одного следа о причине ни в одном логе.
    """
    if not _connect(30.0):
        return "к Ray не подключиться"
    try:
        import ray

        части = []
        for node in ray.nodes():
            части.append("{} {}:{} alive={}".format(
                node.get("NodeID", "?")[:12],
                node.get("NodeManagerAddress", "?"),
                node.get("NodeManagerPort", "?"),
                node.get("Alive")))
        return "; ".join(части) or "узлов не видно"
    except Exception as exc:
        return f"спросить не вышло: {exc}"


def stop_node(temp_dir: str = "") -> None:
    """Снять процессы Ray ЭТОЙ сессии — и только их.

    `ray stop --force` здесь не годится: он снимает все процессы Ray этого
    пользователя на машине, а два ранга на одном узле работают под одним uid.
    Упавший ранг такой уборкой убивал бы голову живого соседа, и тот потом
    стоял бы, ожидая кластер, которого уже нет.

    Полагаться на агента тоже нельзя, хотя раньше так и было. Он снимает группу
    процессов задачи, а Ray заводит свои демоны в отдельной сессии — SIGTERM
    группе до них не доходит. Пережившие уборку gcs и raylet держат порты
    своего окна, и следующий кластер, которому досталось то же окно, встать уже
    не может: снаружи это выглядит как «оба узла running, а рангов нет».

    Отличаем своих по временному каталогу: он у каждой задачи свой, и Ray
    вписывает путь к сессии в командную строку каждого своего процесса.
    """
    маркер = temp_dir or os.environ.get("RAY_TMPDIR") or os.environ.get("LOOMA_TASK_TMP")
    if not маркер:
        return
    try:
        import psutil
    except ImportError:
        logger.debug("psutil недоступен: процессы Ray останутся агенту")
        return
    # Мимо: свой процесс и всё его дерево вверх. Путь сессии стоит в командной
    # строке и у нас самих, и у агента, который нас запустил, — а найденное
    # здесь снимается без разговоров.
    родня = {os.getpid()}
    try:
        родня |= {p.pid for p in psutil.Process().parents()}
    except Exception:
        pass
    свои = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            pid = proc.info["pid"]
            cmdline = " ".join(proc.info.get("cmdline") or ())
            name = proc.info.get("name") or ""
        except Exception:
            continue
        if pid in родня or маркер not in cmdline:
            continue
        # Одного маркера мало: путь сессии упоминает кто угодно, кто про неё
        # говорит, — вплоть до оболочки, в которой набрали команду. Проверено:
        # без этого условия уборка сняла посторонний процесс.
        if not any(знак in name or знак in cmdline for знак in RAY_PROCESSES):
            continue
        свои.append(proc)
    if not свои:
        return
    logger.info("снимаю %d процессов Ray этой сессии", len(свои))
    for proc in свои:
        try:
            proc.terminate()
        except Exception:
            continue
    _, живые = psutil.wait_procs(свои, timeout=10)
    for proc in живые:
        try:
            proc.kill()
        except Exception:
            continue
