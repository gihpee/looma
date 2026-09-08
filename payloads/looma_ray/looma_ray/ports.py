"""Кто на каком порту. Заодно — то, как ранги находят друг друга.

Ray устроен не звездой: воркеры ходят не только к голове, но и друг к другу за
объектами. Значит каждому рангу нужен адрес каждого — а спрашивать его негде,
потому что оркестратор в этот разговор не входит.

Поэтому адресов не ищут, их **вычисляют**. У ранга N свой непересекающийся
диапазон, и любой ранг получает порты любого другого арифметикой, ничего ни у
кого не спрашивая.

Из этого следует главное свойство: на одной машине всё работает сразу и без
посредников — ранги просто разговаривают по настоящему локалхосту. Между
машинами те же самые адреса начинает обслуживать агент, подставляя туда
туннель до нужного пира, и ни строчки здесь менять не надо.

Диапазоны портов И разный адрес на петле у каждого ранга — вместе, а не вместо
друг друга.

Диапазоны нужны потому, что часть компонентов Ray биндится на 0.0.0.0 и
занимает свой порт на всех адресах сразу, включая чужие. С непересекающимися
диапазонами такого столкновения не бывает, и два ранга уживаются на одной
машине.

Адреса нужны потому, что одного локалхоста не хватило. Ray записывает узел в
кластер под тем адресом, который сам себе выбрал, и голова потом проверяет по
нему живость и раздаёт работу. `127.0.0.1` он для этого не принимает: со стенда
он переписывал его в адрес машины — 192.168.2.84 у одного узла, 10.124.10.11 у
другого. Каждый узел записывался адресом СВОЕЙ локальной сети, недостижимым с
чужой машины, и голова после пяти неудачных проверок объявляла узел мёртвым.

Адрес вида `127.0.0.N` Ray оставляет как есть — проверено там же. А вся сеть
127.0.0.0/8 на Linux поднята на петле по умолчанию, так что такой адрес
существует на КАЖДОЙ машине группы и биндится без всяких прав. Ранг N живёт по
`127.0.0.<N+2>` и там же его ищут соседи: у себя — настоящий Ray, на других
машинах — слушатель агента с туннелем.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

# Начало и шаг. Выше эфемерного диапазона Linux не лезем, ниже 1024 тоже:
# задача работает непривилегированной и низкий порт занять не сможет.
BASE = int(os.environ.get("LOOMA_RAY_PORT_BASE", "20000"))
STRIDE = int(os.environ.get("LOOMA_RAY_PORT_STRIDE", "100"))
# Сколько в конце диапазона отдать рабочим процессам Ray. Их много и они
# приходят-уходят, поэтому им отдаётся всё, что осталось после служебных.
FIRST_WORKER_OFFSET = 10
# Сколько портов в конце окна отдать КЛИЕНТУ, а не Ray.
#
# Ради того, чтобы межузловой обмен был не только у Ray. Всё, что клиент
# захочет связать между узлами сам — TCPStore у torch, свой сокет, gRPC, —
# упирается в один и тот же вопрос: какой порт видно с соседней машины.
# Ответ должен существовать заранее, потому что проброс поднимается ДО того,
# как клиентский код вообще запустится.
CLIENT_PORTS = int(os.environ.get("LOOMA_RAY_CLIENT_PORTS", "30"))
# Верхняя граница окна: выше начинается эфемерный диапазон Linux, и занимать
# оттуда — значит однажды столкнуться с чужим исходящим соединением.
WINDOW_END = int(os.environ.get("LOOMA_RAY_PORT_WINDOW_END", "32000"))
# Границы адресов на петле. Первый — второй по счёту: 127.0.0.1 Ray под узел не
# отдаёт, а последний в /8 занимать не будем по той же причине, по какой не
# занимают широковещательный.
LOOPBACK_FIRST = int(os.environ.get("LOOMA_RAY_LOOPBACK_FIRST", "2"))
LOOPBACK_LAST = 254


class PortsRefused(ValueError):
    """Так разложить порты нельзя, и вот почему."""


@dataclass(frozen=True)
class RankPorts:
    """Порты одного ранга. Считаются, а не выдаются."""

    rank: int
    gcs: int                 # голова; у остальных рангов не используется
    node_manager: int
    object_manager: int
    runtime_env_agent: int
    dashboard_listen: int
    dashboard_grpc: int
    metrics: int
    client_server: int      # зарезервирован; см. cluster.py
    worker_first: int
    worker_last: int
    # Хвост окна, отданный клиентскому коду. Ray сюда не лезет: его верхняя
    # граница рабочих портов подрезана ровно на это.
    client_first: int
    client_last: int

    def crossing(self) -> List[int]:
        """Порты, до которых обязаны дотянуться ДРУГИЕ узлы.

        Только они нуждаются в туннеле; остальное Ray дергает у себя же на
        локалхосте, и проброс для них был бы работой впустую.

        Клиентское окно тоже здесь, и это не щедрость: связать что-то между
        узлами САМОМУ клиенту иначе нечем. Проброс поднимается до запуска его
        кода, значит порты обязаны быть известны заранее — выбрать их потом
        уже нельзя.
        """
        return [self.gcs, self.node_manager, self.object_manager,
                *range(self.worker_first, self.worker_last + 1),
                *range(self.client_first, self.client_last + 1)]

    def local_only(self) -> List[int]:
        return [self.runtime_env_agent, self.dashboard_listen,
                self.dashboard_grpc, self.metrics, self.client_server]


def group_base(size: int, *, base: int = 0, stride: int = 0) -> int:
    """Своё окно портов для каждого кластера, а не одно на всех.

    Со стенда: кластер, оставшийся от прошлой попытки, занимал те же порты,
    и голова нового подключалась К НЕМУ — после чего падала на несовпадении
    имени сессии. Причина при этом называлась так, что искать её шли в свой
    код, а не в список процессов.

    Окно выбирается по идентификатору группы, поэтому все ранги считают его
    одинаково и ни у кого ничего не спрашивают — как и всё остальное здесь.
    Совпадение окон возможно, но редко, и брошенный кластер перестаёт быть
    ловушкой по умолчанию.
    """
    base = base or BASE
    stride = stride or STRIDE
    group_id = os.environ.get("LOOMA_GROUP_ID", "").strip()
    width = max(1, size) * stride
    slots = max(1, (WINDOW_END - base) // width)
    if not group_id or slots <= 1:
        return base
    import hashlib

    slot = int(hashlib.sha256(group_id.encode()).hexdigest()[:8], 16) % slots
    return base + slot * width


def ports_for(rank: int, *, base: int = 0, stride: int = 0) -> RankPorts:
    """Разложить диапазон ранга. Одинаково на всех узлах — в этом смысл."""
    if rank < 0:
        raise PortsRefused(f"ранг не может быть отрицательным ({rank})")
    base = base or BASE
    stride = stride or STRIDE
    if stride <= FIRST_WORKER_OFFSET + CLIENT_PORTS:
        raise PortsRefused(
            f"шаг {stride} не оставляет места рабочим портам: служебные "
            f"занимают первые {FIRST_WORKER_OFFSET}, клиентские — последние "
            f"{CLIENT_PORTS}")
    start = base + rank * stride
    if start + stride > 65536:
        raise PortsRefused(
            f"ранг {rank} при основании {base} и шаге {stride} вышел за 65535; "
            "уменьшите LOOMA_RAY_PORT_STRIDE или основание")
    return RankPorts(
        rank=rank,
        gcs=start,
        node_manager=start + 1,
        object_manager=start + 2,
        runtime_env_agent=start + 3,
        dashboard_listen=start + 4,
        dashboard_grpc=start + 5,
        metrics=start + 6,
        client_server=start + 7,
        worker_first=start + FIRST_WORKER_OFFSET,
        # Подрезано на клиентское окно: Ray занимает порты из своего диапазона
        # по мере надобности, и пересечение выглядело бы как случайный отказ
        # клиентского сокета раз в несколько запусков.
        worker_last=start + stride - 1 - CLIENT_PORTS,
        client_first=start + stride - CLIENT_PORTS,
        client_last=start + stride - 1,
    )


def loopback_for(rank: int) -> str:
    """Адрес ранга на петле. Один и тот же на всех машинах группы — в этом суть.

    Считается от ранга, а не выдаётся: как и порты, адрес каждый узел выводит
    сам, ни у кого ничего не спрашивая.

    Со второго, а не с первого: `127.0.0.1` Ray под узел не отдаёт — переписывает
    его в адрес машины. Заодно он остаётся свободен под всё остальное, что
    привыкло жить на локалхосте.
    """
    if rank < 0:
        raise PortsRefused(f"ранг не может быть отрицательным ({rank})")
    if rank > LOOPBACK_LAST - LOOPBACK_FIRST:
        raise PortsRefused(
            f"ранг {rank} не помещается в 127.0.0.x: адресов там "
            f"{LOOPBACK_LAST - LOOPBACK_FIRST + 1}")
    return f"127.0.0.{LOOPBACK_FIRST + rank}"


def hosts_for_group(size: int) -> dict:
    """Ранг → его адрес. То, что агент поднимает у себя вместо соседей."""
    return {rank: loopback_for(rank) for rank in range(size)}


def head_address(base: int = 0, stride: int = 0) -> str:
    """Куда подключаются все. Голова — всегда ранг 0, и это не соглашение
    между узлами, а следствие того же расчёта."""
    return f"{loopback_for(0)}:{ports_for(0, base=base, stride=stride).gcs}"


def client_env(size: int, rank: int, *, base: int = 0, stride: int = 0) -> dict:
    """Чем клиентский код найдёт соседей. Всё, что для этого нужно, и ничего
    сверх.

    Выставляется ДО `ray start` — и в этом весь смысл. Воркеры Ray наследуют
    окружение своего raylet, а raylet запускается нашим подпроцессом; значит
    переменная, поставленная здесь, доедет до каждого актора на этом узле.
    Поставить её позже было бы некуда: у актора своё окружение, и мы в него не
    входим.

    `MASTER_ADDR` и `MASTER_PORT` названы так, как их ищет torch: это его
    соглашение, и переименовывать его в своё значило бы заставить клиента
    писать лишнюю строку ради ничего. Граница у них честная и описана в
    docs/RAY_DEMO.md: точка встречи через них работает, а коллективы gloo и
    NCCL между МАШИНАМИ — нет, потому что свои сокеты они открывают на
    случайных портах, которые нельзя пробросить заранее.
    """
    base = base or group_base(size, stride=stride)
    ports = ports_for(rank, base=base, stride=stride)
    head = ports_for(0, base=base, stride=stride)
    return {
        "MASTER_ADDR": loopback_for(0),
        "MASTER_PORT": str(head.client_first),
        "WORLD_SIZE": str(size),
        # Ранг Ray, а не ранг процесса torch: совпадают они только пока на узле
        # один воркер. Клиенту, который поднимает несколько, считать свой ранг
        # придётся самому — отсюда и NODE_RANK рядом.
        "RANK": str(rank),
        "NODE_RANK": str(rank),
        # Адрес каждого ранга — тот же на всех машинах группы (см. loopback_for).
        "LOOMA_RANK_ADDRS": ",".join(
            f"{r}={loopback_for(r)}" for r in range(size)),
        # Порты, которые на этом узле ВИДНЫ соседям. Занимать другие можно, но
        # достучаться до них будет нельзя, и выглядеть это будет как обрыв.
        "LOOMA_PORTS": f"{ports.client_first}-{ports.client_last}",
    }


def crossing_for_group(size: int, *, base: int = 0, stride: int = 0) -> dict:
    """Что агенту предстоит пробросить: ранг → его внешние порты.

    Считается здесь, а не в агенте: агент не должен знать, как Ray раскладывает
    порты, — иначе смена версии Ray станет обновлением парка.
    """
    base = base or group_base(size, stride=stride)
    return {rank: ports_for(rank, base=base, stride=stride).crossing()
            for rank in range(size)}
