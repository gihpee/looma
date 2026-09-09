"""Адреса рангов на петле — там, где их нет по умолчанию.

Кластер Ray собирается на адресах вида `127.0.0.<N+2>`: у каждого ранга свой, и
он означает одно и то же на всех машинах группы (payloads/looma_ray/ports.py).
На Linux это ничего не стоит — там вся сеть 127.0.0.0/8 поднята на петле, и
любой такой адрес биндится сразу.

На macOS назначен ровно один адрес, `127.0.0.1`. Остальные не существуют:

    127.0.0.2: Can't assign requested address

Со стенда это выглядело как `HTTP Error 502` в ответ на запрос проброса — то
есть отказом агента без единого слова о том, что именно не сошлось.

Поэтому адрес заводится. Это изменение сетевой настройки чужой машины, и
относиться к нему надо соответственно: заводим ровно те адреса, что нужны
группе, помним каждый заведённый нами и убираем их, когда агент уходит. Чужие
(заведённые до нас или кем-то ещё) не трогаем никогда.
"""

from __future__ import annotations

import logging
import platform
import socket
import subprocess
import threading
from typing import Set

logger = logging.getLogger("looma_agent.tasks.loopback")

IFCONFIG = "/sbin/ifconfig"
INTERFACE = "lo0"

# Что мы завели сами. Только эти адреса и снимаем: адрес, поднятый владельцем
# машины для своих дел, к нам отношения не имеет.
_ours: Set[str] = set()
_lock = threading.Lock()


def needed() -> bool:
    """Нужно ли вообще что-то делать на этой системе."""
    return platform.system() == "Darwin"


def available(address: str) -> bool:
    """Можно ли уже сейчас слушать на этом адресе."""
    probe = socket.socket()
    try:
        probe.bind((address, 0))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def ensure(address: str) -> bool:
    """Сделать адрес пригодным для bind. False — не вышло, и вот почему в логе.

    Идемпотентно: уже существующий адрес не трогаем и в свои не записываем —
    иначе, уходя, сняли бы чужое.
    """
    if not needed() or available(address):
        return True
    if not address.startswith("127."):
        # Заводить на петле что-то за пределами петли мы не станем ни при
        # каких обстоятельствах: это уже не наша машина.
        logger.warning("адрес %s не с петли; не завожу", address)
        return False
    try:
        subprocess.run([IFCONFIG, INTERFACE, "alias", address, "up"],
                       check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        logger.warning(
            "не удалось поднять %s на %s (%s): %s. Кластер на нескольких "
            "машинах здесь не соберётся — для этого агенту нужны права root",
            address, INTERFACE, exc,
            detail.decode("utf-8", "replace").strip())
        return False
    with _lock:
        _ours.add(address)
    logger.info("поднял %s на %s для ранга кластера", address, INTERFACE)
    return True


def release_all() -> None:
    """Снять всё, что подняли мы. Вызывается, когда агент уходит.

    Не по задачам, а разом: один адрес может держать несколько групп, и
    считать ссылки ради настройки, которая всё равно исчезнет при перезагрузке,
    значит завести отдельный источник ошибок.
    """
    with _lock:
        addresses, _ours_copy = sorted(_ours), set(_ours)
        _ours.clear()
    for address in addresses:
        try:
            subprocess.run([IFCONFIG, INTERFACE, "-alias", address],
                           check=False, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
    if addresses:
        logger.info("снял с %s адреса: %s", INTERFACE, ", ".join(addresses))
