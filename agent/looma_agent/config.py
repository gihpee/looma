"""Everything the agent needs to know before it can do anything.

One rule: a node owner passes ONE opaque string and nothing else. The
orchestrator address, the node's secret and its identity all come out of the
join key. Every other setting has a working default, because a setting with no
default is a support ticket from someone who just wanted to lend us a GPU.
"""

from __future__ import annotations

import argparse
import os
import platform
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    join_key: str
    node_id: str
    region: str
    root: Path
    heartbeat_interval_s: float
    reconnect_delay_s: float

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    @property
    def key_file(self) -> Path:
        """Где лежит ключ, если его не передали при запуске.

        Нужен там, где агента ставят раньше, чем вводят ключ, — то есть на
        пользовательской машине: установщик кладёт демон и уходит, а ключ
        провайдер вставляет потом, в панели.
        """
        return self.root / "join.key"

    @property
    def pause_file(self) -> Path:
        """Пока он есть, узел работы не берёт.

        Файлом, а не выключением демона: остановить системный демон может
        только root, а панель работает под обычным пользователем, и просить у
        него пароль каждый раз — плохая цена за кнопку, которую нажимают
        ежедневно. Каталог узла открыт группе admin на запись, чего для файла
        достаточно.

        Заодно решение переживает перезагрузку: владелец, отключивший машину на
        ночь, не обнаружит её утром снова в работе.
        """
        return self.root / "paused"

    @property
    def envs_dir(self) -> Path:
        return self.root / "envs"

    @property
    def models_dir(self) -> Path:
        """Веса. Рядом с окружениями и по той же причине: и то и другое
        переживает задачу, которая их запросила."""
        return self.root / "models"


def default_root() -> str:
    """Где узел держит свои данные.

    На macOS — там, где системе положено держать данные служебных демонов, а не
    в /var/lib: последний на Mac существует, но принадлежит совсем другому
    порядку вещей, и всё, что смотрит на систему со стороны — Time Machine,
    антивирусы, сама Apple, — ищет наши файлы не там.
    """
    return ("/Library/Application Support/Looma" if platform.system() == "Darwin"
            else "/var/lib/looma")


def parse_args(argv=None) -> Config:
    parser = argparse.ArgumentParser(prog="looma-agent")
    parser.add_argument(
        "--key",
        default=os.environ.get("LOOMA_JOIN_KEY", ""),
        help="join key issued by the orchestrator; carries its address and this node's secret",
    )
    parser.add_argument(
        "--node-id",
        default=os.environ.get("LOOMA_NODE_ID", ""),
        help="stable name for this node (default: hostname, plus a GPU suffix when this "
             "agent was given only some of the machine's cards)",
    )
    parser.add_argument("--region", default=os.environ.get("LOOMA_REGION", "default"))
    parser.add_argument(
        "--root",
        default=os.environ.get("LOOMA_ROOT", default_root()),
        help="where task directories and the environment cache live",
    )
    parser.add_argument("--heartbeat-interval", type=float, default=5.0)
    parser.add_argument("--reconnect-delay", type=float, default=3.0)
    args = parser.parse_args(argv)
    return Config(
        join_key=args.key.strip(),
        node_id=args.node_id.strip(),
        region=args.region,
        root=Path(args.root),
        heartbeat_interval_s=args.heartbeat_interval,
        reconnect_delay_s=args.reconnect_delay,
    )
