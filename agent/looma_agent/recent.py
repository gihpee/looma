"""Последние строки собственного лога агента — в памяти.

Зачем. Логи задач оркестратор забирает с любого узла, а логи самого агента до
сих пор не забирал ниоткуда. Всё, что агент знает про свои туннели, соседей и
p2p, было доступно только тому, у кого есть shell на этой машине. Для парка из
чужих домашних компьютеров это тупик: shell есть у владельца, а разбирается в
неполадке оператор — и половина расследования идёт вслепую.

Почему в памяти, а не файлом. Узел — чужая машина, и писать на её диск ради
нашего удобства мы не станем: у неё свой владелец, свой диск и свои
представления о том, что там должно лежать. Кольцо на несколько тысяч строк
стоит пару мегабайт и исчезает вместе с процессом — ровно как и должно.
"""

from __future__ import annotations

import collections
import logging
import os
import threading

#: Сколько строк держать. Хватает, чтобы застать сборку кластера целиком вместе
#: с предысторией, и мало, чтобы не думать о памяти.
KEEP_LINES = int(os.environ.get("LOOMA_LOG_KEEP_LINES", "4000"))


class Recent(logging.Handler):
    """Обработчик, который ничего не печатает, а помнит.

    Ставится рядом с обычным выводом, а не вместо него: `docker logs` на самой
    машине должен продолжать работать как раньше.
    """

    def __init__(self, keep: int = KEEP_LINES) -> None:
        super().__init__()
        self._lines: collections.deque = collections.deque(maxlen=keep)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:      # noqa: BLE001 — сбой записи лога не имеет права
            return             # ронять то, о чём он пишет
        with self._lock:
            self._lines.append(line)

    def tail(self, lines: int = 200) -> str:
        with self._lock:
            собрано = list(self._lines)
        if lines > 0:
            собрано = собрано[-lines:]
        return "\n".join(собрано)


#: Один на процесс: его ставит _setup_logging, а спрашивает обработчик команд.
BUFFER = Recent()


def install(formatter: logging.Formatter) -> None:
    """Подключить кольцо к корневому журналу."""
    BUFFER.setFormatter(formatter)
    logging.getLogger().addHandler(BUFFER)
