"""Обычное TCP-соединение поверх p2p — для тех, кто не умеет иначе.

Всё остальное в Looma адресуется по РАНГУ и обходится сообщениями: задача шлёт
на loopback, агент решает куда. Этого хватает конвейеру и хватило бы почти
всему, что мы напишем сами.

Не хватает чужому софту. Ray собирает кластер, обращаясь к соседям по адресу и
порту, и переписать его нельзя — в этом же и смысл, чтобы код клиента работал
как есть. Значит между узлами нужен настоящий байтовый поток.

Как он сделан. Lattica даёт серверный стрим (`rpc_stream_iter`: один запрос —
много ответов) и унарный вызов. Полного дуплекса нет, поэтому направления
разведены:

    сосед → нам     serving-стрим, сырые байты, без перекодировки
    нам → соседу    унарные вызовы, данные в base64 (dict иначе не переживёт)

Отсюда главное свойство и главное ограничение: чтение дешёвое, запись стоит
round trip на порцию. Для управляющего обмена Ray это незаметно, для перекачки
объектов — нет, и настоящая цена меряется, а не предполагается (docs/RAY.md).
"""

from __future__ import annotations

import base64
import logging
import socket
import threading
from typing import Callable, Dict, Optional

logger = logging.getLogger("looma_agent.p2p.tunnel")

# Порция, которой ходят данные. Крупнее — меньше round trip'ов на мегабайт;
# слишком крупно — сообщение упирается в лимиты транспорта и растёт задержка
# на мелком обмене, которого у Ray большинство.
CHUNK = 64 * 1024
# Сколько ждать локальное соединение на той стороне. Целевой процесс — сосед по
# машине, так что это либо мгновенно, либо не будет вовсе.
CONNECT_TIMEOUT_S = 5.0
# Сколько ждать ответа соседа на унарный вызов. Целые секунды: привязка
# отвергает дробные, а мок, который их принимает, прячет это до первой встречи
# с настоящим пиром.
CALL_TIMEOUT_S = 30
# Потолок на число одновременных туннелей через один узел. Ray открывает много
# соединений, но не бесконечно: без потолка чужая ошибка становится нашей.
MAX_CONNECTIONS = int(__import__("os").environ.get("LOOMA_TUNNEL_MAX", "512"))


class TunnelRefused(RuntimeError):
    """Туннель не открылся, и вот почему."""


def _own_address() -> str:
    """Адрес этой машины в её сети. Пусто — если его нет.

    Тем же приёмом, что и в tasks/forward.py: спросить у таблицы маршрутизации,
    с какого адреса она пошла бы наружу. Пакет при этом не уходит.
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


class Endpoint:
    """Наша сторона: принимает соединения, которые открывают соседи.

    Живёт в агенте и ничего не знает ни про Ray, ни про группы: ей называют
    порт, она открывает к нему локальное соединение и возит байты.
    """

    def __init__(self, *, allow: Optional[Callable[[int], bool]] = None,
                 host_for: Optional[Callable[[int], str]] = None) -> None:
        # Что разрешено открывать. Без этого сосед мог бы дотянуться до любого
        # порта на этой машине, включая порты чужих задач и самого агента.
        self.allow = allow or (lambda _port: False)
        # На каком адресе искать этот порт у себя. Обычно локалхост, но у
        # кластера Ray каждый ранг живёт на своём адресе петли (см.
        # tasks/forward.py), и там его слушают только по нему — соединение на
        # 127.0.0.1 отвергается, а выглядит это как «сосед не отвечает».
        self.host_for = host_for or (lambda _port: "127.0.0.1")
        self._conns: Dict[str, socket.socket] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------- вызовы соседа
    def connect(self, conn_id: str, port: int) -> dict:
        """Открыть локальное соединение. Отдельным вызовом, а не внутри стрима:
        так сосед узнаёт об отказе сразу и не пишет в никуда."""
        if not self.allow(port):
            return {"ok": False, "error": f"порт {port} не открыт для соседей"}
        with self._lock:
            if len(self._conns) >= MAX_CONNECTIONS:
                return {"ok": False, "error": "слишком много туннелей на этом узле"}
            if conn_id in self._conns:
                return {"ok": False, "error": f"туннель {conn_id} уже есть"}
        # Сначала адрес, который назвала задача, потом обычный локалхост.
        # Гадать, где именно софт задачи поднял слушателя, бесполезно: Ray
        # держит ранг на адресе своего ранга, а клиентский вход — на другом, и
        # какой где, зависит от версии. Со стенда: кластер собрался и работал,
        # а `looma-connect` получал «127.0.0.2:25607 не отвечает» — там сидел
        # локалхост.
        # Третьим — адрес самой машины: часть служб Ray биндится на него, а не
        # на петлю, и тогда оба локальных адреса отвечают отказом о работающем
        # сервере.
        candidates = [self.host_for(port) or "127.0.0.1"]
        for спутник in ("127.0.0.1", _own_address()):
            if спутник and спутник not in candidates:
                candidates.append(спутник)
        sock, refusals = None, []
        for host in candidates:
            try:
                sock = socket.create_connection((host, port),
                                                timeout=CONNECT_TIMEOUT_S)
                break
            except OSError as exc:
                refusals.append(f"{host} ({exc.strerror or exc})")
        if sock is None:
            # Все адреса, а не последний: иначе из отказа не видно, где вообще
            # искали, и «не отвечает 192.168.1.5» читается как проблема сети,
            # хотя проверялись ещё два локальных.
            return {"ok": False,
                    "error": f"порт {port} не отвечает нигде: " + ", ".join(refusals)}
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self._lock:
            self._conns[conn_id] = sock
        return {"ok": True}

    def read(self, conn_id: str):
        """Отдавать всё, что говорит локальная сторона. Это тело стрима."""
        sock = self._get(conn_id)
        if sock is None:
            return
        try:
            while True:
                piece = sock.recv(CHUNK)
                if not piece:
                    return
                yield piece
        except OSError:
            return
        finally:
            self.close(conn_id)

    def write(self, conn_id: str, data: bytes) -> dict:
        sock = self._get(conn_id)
        if sock is None:
            return {"ok": False, "error": "нет такого туннеля"}
        try:
            sock.sendall(data)
        except OSError as exc:
            self.close(conn_id)
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def close(self, conn_id: str) -> dict:
        with self._lock:
            sock = self._conns.pop(conn_id, None)
        _shutdown(sock)
        return {"ok": True}

    def close_all(self) -> None:
        with self._lock:
            socks = list(self._conns.values())
            self._conns.clear()
        for sock in socks:
            _shutdown(sock)

    def _get(self, conn_id: str) -> Optional[socket.socket]:
        with self._lock:
            return self._conns.get(conn_id)

    @property
    def open_count(self) -> int:
        with self._lock:
            return len(self._conns)


def pump(local: socket.socket, remote: "RemoteSide", *, closed: threading.Event) -> str:
    """Возить байты между локальным сокетом и соседом, пока кто-то не кончится.

    Два направления — два потока: чтение из сокета блокирующее, и совместить
    его с чтением из стрима в одном потоке нельзя, не поставив одно в
    зависимость от другого.

    Возвращает причину, по которой всё кончилось. Раньше обе стороны глотали
    любую ошибку молча — и оборванный туннель не оставлял ни следа. Со стенда:
    кластер Ray собирался, через несколько минут разваливался, и в логах при
    этом не было ничего, а состояние показывалось как «ok». Разбираться
    приходилось по косвенным признакам, потому что прямых не существовало.
    """
    # По причине на каждое направление, а не одна на обе. Потоки кончаются
    # почти одновременно, и запись только первого оставляла второй невидимым:
    # «Ray закрыл соединение» и «сосед перестал отвечать» — разные диагнозы, а
    # выглядели одинаково, потому что побеждал тот, кто успел первым.
    reason: dict = {}

    def why(side: str, text: str) -> None:
        reason.setdefault(side, text)

    def outbound() -> None:
        try:
            while not closed.is_set():
                piece = local.recv(CHUNK)
                if not piece:
                    why("наружу", "местная сторона закрыла соединение")
                    break
                if not remote.write(piece):
                    why("наружу", "сосед не принял данные")
                    break
        except OSError as exc:
            why("наружу", f"чтение из местного сокета: {exc}")
        finally:
            closed.set()

    def inbound() -> None:
        try:
            for piece in remote.read():
                if closed.is_set():
                    break
                try:
                    local.sendall(piece)
                except OSError as exc:
                    # Обязательно отдельно от ошибок стрима: «Broken pipe» здесь
                    # означает, что местная сторона закрыла соединение, а не что
                    # сосед пропал. В одном обработчике подпись обвиняла не того,
                    # и по ней шли искать причину на другой машине.
                    why("внутрь", f"местная сторона закрыла соединение: {exc}")
                    return
            why("внутрь", "поток от соседа кончился")
        except Exception as exc:
            why("внутрь", f"поток от соседа: {exc}")
        finally:
            closed.set()

    threads = [
        threading.Thread(target=outbound, name="tunnel-out", daemon=True),
        threading.Thread(target=inbound, name="tunnel-in", daemon=True),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _shutdown(local)
    remote.close()
    # Оба направления, а не одно: пара «наружу … / внутрь …» отвечает на
    # вопрос, кто из двоих кончился первым и по своей ли воле.
    return " | ".join(f"{side}: {reason[side]}"
                      for side in ("наружу", "внутрь") if side in reason) \
        or "обе стороны замолчали"


class RemoteSide:
    """Сосед на том конце. Тонкая обёртка над стабом lattica."""

    def __init__(self, stub, conn_id: str, port: int) -> None:
        self.stub = stub
        self.conn_id = conn_id
        self.port = port
        self._closed = False

    def open(self) -> None:
        answer = _settled(self.stub.tunnel_connect(
            {"conn": self.conn_id, "port": self.port}))
        if not answer.get("ok", False):
            raise TunnelRefused(answer.get("error") or "сосед отказал без причины")

    def read(self):
        """Стрим отдаёт итератор сразу — в отличие от унарных вызовов."""
        return self.stub.tunnel_open({"conn": self.conn_id})

    def write(self, data: bytes) -> bool:
        answer = _settled(self.stub.tunnel_write({
            "conn": self.conn_id, "data": base64.b64encode(data).decode()}))
        return bool(answer.get("ok", False))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.stub.tunnel_close({"conn": self.conn_id})
        except Exception:
            logger.debug("закрытие туннеля %s не прошло", self.conn_id, exc_info=True)


def _settled(answer, timeout_s: int = CALL_TIMEOUT_S) -> dict:
    """Дождаться ответа унарного вызова.

    Стаб отдаёт future, а не результат. Читать его как словарь — значит
    принимать ЛЮБОЙ ответ за успех: отказ соседа выглядит ровно так же, как
    согласие, и обнаруживается на порядок позже, чем случился.
    """
    if hasattr(answer, "result"):
        try:
            answer = answer.result(timeout=timeout_s)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return answer if isinstance(answer, dict) else {"ok": True}


def _shutdown(sock: Optional[socket.socket]) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass
