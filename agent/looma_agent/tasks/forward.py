"""Порты соседей, притворяющиеся местными.

Задача, которая ходит к соседям по РАНГУ, ничего этого не требует: агент возит
её сообщения сам. Требует чужой софт — прежде всего Ray, который собирает
кластер, обращаясь к адресам и портам, и переписать который нельзя, потому что
весь смысл в том, чтобы код клиента работал как есть.

Приём такой. У ранга N свой адрес и свой непересекающийся диапазон портов;
агент на каждом узле слушает у себя адреса ЧУЖИХ рангов и возит принятые
соединения в туннель до нужного пира. Все узлы видят одинаковую картину «ранг M
живёт вот по этому адресу», и Ray про NAT не узнаёт никогда.

Адрес приходит вместе с портами и от того же, кто их разложил, — по той же
причине: его выбирает софт задачи. Задача постарше адресов не присылает; для
неё остаётся прежнее поведение (локалхост и адрес машины), и оно работает,
пока ранги на одной машине.

Два следствия, оба существенные:

**Ранги на одной машине не проксируются вовсе.** Их Ray уже слушает эти порты
по-настоящему, и вклиниться туда значило бы не ускорить, а сломать: порт занят,
слушатель не встанет. Так что локальный сосед — это просто отсутствие работы.

**Агент не знает раскладку портов.** Её присылает сама задача (`/forward` на
канале), потому что раскладку определяет версия Ray, а не версия агента —
иначе обновление Ray стало бы обновлением парка.
"""

from __future__ import annotations

import logging
import selectors
import socket
import time
import threading
import uuid
from typing import Callable, Dict, List, Optional

from looma_agent.p2p.tunnel import RemoteSide, TunnelRefused, pump
from looma_agent.tasks import loopback

logger = logging.getLogger("looma_agent.tasks.forward")

# Сколько соединений держать в очереди на каждом слушателе. Ray открывает их
# пачками при сборке кластера.
BACKLOG = 32


def own_address() -> str:
    """Адрес, который Ray считает адресом ЭТОЙ машины. Пусто — если его нет.

    Тем же приёмом, каким его выбирает сам Ray: спросить у таблицы
    маршрутизации, с какого адреса она пошла бы наружу. Пакет при этом не
    уходит — UDP-connect локален.

    Зачем это здесь. Ray переписывает loopback в `--address` на адрес машины и
    флаг `--node-ip-address` этому не мешает — проверено на стенде: из
    `--address 127.0.0.1:65000` получается `10.124.10.11:65000`. То есть
    присоединяющийся ранг стучится НЕ туда, где мы его ждём. Спорить с этим
    нечем, поэтому слушаем и там тоже: адрес переписан — попадает в тот же
    туннель.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 53))
        found = probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()
    return "" if not found or found.startswith("127.") else found


class ForwardRefused(RuntimeError):
    """Пробросить не получилось, и вот почему."""


class Forwarder:
    """Слушатели чужих портов на этом узле, по задаче.

    Одна нить приёма на всех: диапазон ранга — это десятки портов, а группа из
    четырёх узлов даёт под три сотни слушателей. Нить на каждый — три сотни
    нитей, спящих в accept, ради работы, которой хватает одной.
    """

    def __init__(self, *, stub_for: Optional[Callable[[str], object]] = None,
                 allow_local: Optional[Callable[[List[int]], None]] = None,
                 addresses_of: Optional[Callable[[str], List[str]]] = None) -> None:
        # Чем спросить, что DHT знает о соседе. Без этого отказ туннеля не
        # отличить от ненайденного адреса, а это разные поломки.
        self.addresses_of = addresses_of or (lambda _peer: [])
        # Как достать стаб соседа. None означает «p2p нет» — тогда пробрасывать
        # некуда, и мы говорим об этом сразу, а не молча слушаем впустую.
        self.stub_for = stub_for
        # Чем открыть СВОИ порты входящим: соседи тянутся к нам так же.
        self.allow_local = allow_local or (lambda _ports: None)
        self._sel = selectors.DefaultSelector()
        self._by_task: Dict[str, List[socket.socket]] = {}
        # Живые соединения задачи. Закрыть слушатели мало: то, что уже течёт
        # через них, продолжает жить и после снятия кластера — до тех пор, пока
        # не оборвётся само. На узле это выглядит как чужие туннели, которых
        # никто не открывал.
        self._carrying: Dict[str, List[socket.socket]] = {}
        self._targets: Dict[socket.socket, str] = {}   # слушатель → peer_id
        self._ports: Dict[socket.socket, int] = {}
        self._lock = threading.RLock()
        self._wake_r, self._wake_w = socket.socketpair()
        self._sel.register(self._wake_r, selectors.EVENT_READ)
        self._stop = threading.Event()
        self._loop: Optional[threading.Thread] = None

    # ------------------------------------------------------------- открытие
    def open(self, task_id: str, *, mine: List[int], remote: Dict[int, str],
             ports: Dict[int, List[int]],
             hosts: Optional[Dict[int, str]] = None) -> dict:
        """Начать пробрасывать для этой задачи.

        `mine`   — порты нашего ранга: их надо открыть входящим.
        `remote` — ранг → peer_id тех, кто НЕ на этой машине.
        `ports`  — ранг → его порты, как их посчитала сама задача.
        `hosts`  — ранг → адрес, на котором его ждут. Пусто у задач постарше.
        """
        hosts = hosts or {}
        self.allow_local(list(mine))
        if not remote:
            # Все соседи на этой же машине: их Ray уже слушает эти порты
            # по-настоящему, и наше вмешательство только отняло бы их.
            return {"listening": 0, "ranks": []}
        if self.stub_for is None:
            raise ForwardRefused(
                "на этом узле нет прямого канала до соседей, а без него ранги "
                "друг друга не найдут")

        opened: List[socket.socket] = []
        ports_open = 0
        skipped: List[int] = []
        try:
            for rank, peer_id in sorted(remote.items()):
                where = [hosts[rank]] if rank in hosts else self._hosts()
                for port in ports.get(rank, []):
                    try:
                        opened.extend(self._listen(port, peer_id, where))
                    except ForwardRefused as отказ:
                        # Один занятый порт из девяноста трёх — не повод ронять
                        # задачу целиком. Рабочих портов у Ray шесть десятков,
                        # и занятый он просто обойдёт; а вот без порта головы
                        # кластер не соберётся. Что именно случилось, знает
                        # только сама задача — она одна знает, какой порт чему
                        # служит. Поэтому здесь список, а не отказ.
                        logger.warning("задача %s: %s", task_id, отказ)
                        skipped.append(port)
                        continue
                    ports_open += 1
        except Exception:
            for sock in opened:
                self._drop(sock)
            raise
        with self._lock:
            self._by_task.setdefault(task_id, []).extend(opened)
        self._ensure_loop()
        self._wake()
        # Портов, а не сокетов: у задачи постарше на каждый порт их два —
        # локалхост и адрес машины. Считать сокеты значило бы удвоить число.
        logger.info("задача %s: слушаю %d чужих портов для рангов %s (на %s)",
                    task_id, ports_open, sorted(remote),
                    ", ".join(hosts.get(rank) or "+".join(self._hosts())
                              for rank in sorted(remote)))
        if skipped:
            logger.warning("задача %s: %d портов из %d заняты и пропущены: %s",
                           task_id, len(skipped), ports_open + len(skipped),
                           ", ".join(str(p) for p in skipped[:12]))
        return {"listening": ports_open, "ranks": sorted(remote),
                "skipped": skipped}

    def close(self, task_id: str) -> None:
        with self._lock:
            socks = self._by_task.pop(task_id, [])
            живые = self._carrying.pop(task_id, [])
        for sock in socks:
            self._drop(sock)
        # И то, что уже течёт: иначе снятый кластер оставляет за собой открытые
        # туннели, а потолок на узле общий.
        for sock in живые:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if живые:
            logger.info("задача %s: закрыл %d живых туннелей", task_id, len(живые))
        self._wake()

    def close_all(self) -> None:
        with self._lock:
            tasks = list(self._by_task)
        for task_id in tasks:
            self.close(task_id)
        self._stop.set()
        self._wake()

    @property
    def listening(self) -> int:
        """Сколько ПОРТОВ слушаем, а не сокетов.

        У задачи постарше на порт приходится два сокета — локалхост и адрес
        машины, — и счёт по сокетам удваивал бы число на ровном месте.
        """
        with self._lock:
            return len({self._ports[sock]
                        for socks in self._by_task.values()
                        for sock in socks if sock in self._ports})

    # -------------------------------------------------------------- частное
    def _hosts(self) -> List[str]:
        """Где слушать, когда задача адреса не прислала. Локалхост обязателен,
        адрес машины — если он есть.

        Не 0.0.0.0: это домашняя машина, и открывать порты соседа во все
        интерфейсы разом, включая публичный, мы не станем.
        """
        own = own_address()
        return ["127.0.0.1", own] if own else ["127.0.0.1"]

    def _listen(self, port: int, peer_id: str,
                where: List[str]) -> List[socket.socket]:
        made: List[socket.socket] = []
        for host in where:
            # На macOS из всей петли назначен только 127.0.0.1, и адрес ранга
            # приходится заводить (tasks/loopback.py). На Linux это ничего не
            # делает: там вся 127.0.0.0/8 уже поднята.
            loopback.ensure(host)
            sock = socket.socket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError as exc:
                sock.close()
                for done in made:
                    self._drop(done)
                raise ForwardRefused(
                    f"порт {port} на {host} занят ({exc}); либо ранги делят "
                    "машину и их диапазоны совпали, либо порт остался от "
                    "прошлой группы — посмотрите, кто его держит "
                    f"(lsof -nP -iTCP:{port} -sTCP:LISTEN)") from None
            sock.listen(BACKLOG)
            sock.setblocking(False)
            with self._lock:
                self._targets[sock] = peer_id
                self._ports[sock] = port
            self._sel.register(sock, selectors.EVENT_READ)
            made.append(sock)
        return made

    def _drop(self, sock: socket.socket) -> None:
        try:
            self._sel.unregister(sock)
        except (KeyError, ValueError):
            pass
        with self._lock:
            self._targets.pop(sock, None)
            self._ports.pop(sock, None)
        try:
            sock.close()
        except OSError:
            pass

    def _ensure_loop(self) -> None:
        if self._loop is not None and self._loop.is_alive():
            return
        self._stop.clear()
        self._loop = threading.Thread(target=self._accept_forever,
                                      name="looma-forward", daemon=True)
        self._loop.start()

    def _wake(self) -> None:
        try:
            self._wake_w.send(b"\x00")
        except OSError:
            pass

    def _accept_forever(self) -> None:
        while not self._stop.is_set():
            try:
                events = self._sel.select(timeout=1.0)
            except OSError:
                continue
            for key, _mask in events:
                if key.fileobj is self._wake_r:
                    try:
                        self._wake_r.recv(4096)
                    except OSError:
                        pass
                    continue
                self._accept(key.fileobj)

    def _accept(self, listener: socket.socket) -> None:
        try:
            client, _ = listener.accept()
        except OSError:
            return
        with self._lock:
            peer_id = self._targets.get(listener)
            port = self._ports.get(listener)
        if peer_id is None or port is None:
            client.close()
            return
        client.setblocking(True)
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=self._carry,
                         args=(client, peer_id, port, self._task_of(listener)),
                         name=f"forward-{port}", daemon=True).start()

    def _task_of(self, listener: socket.socket) -> str:
        """Чей это слушатель. Нужно, чтобы туннель на том конце знал, кого
        закрывать при снятии задачи."""
        with self._lock:
            for task_id, socks in self._by_task.items():
                if listener in socks:
                    return task_id
        return ""

    def _carry(self, client: socket.socket, peer_id: str, port: int,
               task_id: str = "") -> None:
        if task_id:
            with self._lock:
                self._carrying.setdefault(task_id, []).append(client)
        try:
            # Внутри try, а не снаружи: получение стаба тоже отказывает — на
            # узле без p2p, например. Снаружи такой отказ улетал в поток приёма
            # незамеченным: ни строчки в логе, ни закрытого сокета, а Ray
            # ждал ответа от соединения, которое никто уже не обслуживает.
            remote = RemoteSide(self.stub_for(peer_id), uuid.uuid4().hex[:12],
                                port, task_id)
            remote.open()
        except (TunnelRefused, Exception) as exc:
            # Отказ соседа — не наша поломка: Ray переоткроет соединение.
            # Но молчать нельзя, иначе «кластер не собрался» останется без
            # единого следа о том, почему.
            # Что именно не получилось: адреса нет вовсе или он есть, но не
            # набирается. В сообщении lattica это неразличимо.
            known = self.addresses_of(peer_id)
            logger.warning("туннель к %s:%d не открылся: %s; DHT знает о нём %s",
                           peer_id[:12], port, exc,
                           ", ".join(known) if known else "НИ ОДНОГО адреса")
            try:
                client.close()
            except OSError:
                pass
            return
        # Долгие соединения — это несущие: связь raylet с головой держится
        # именно ими. Их обрыв разваливает кластер, и знать о нём надо. Короткие
        # Ray открывает и закрывает пачками, они в лог не идут.
        started = time.monotonic()
        try:
            why = pump(client, remote, closed=threading.Event())
        finally:
            if task_id:
                with self._lock:
                    живые = self._carrying.get(task_id)
                    if живые and client in живые:
                        живые.remove(client)
        lived = time.monotonic() - started
        говорить = logger.info if lived >= 10 else logger.debug
        говорить("туннель к %s:%d прожил %.0f с и закрылся: %s",
                 peer_id[:12], port, lived, why)
