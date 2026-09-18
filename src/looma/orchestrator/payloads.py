"""Код, который едет на узел вместе с задачей.

Пакет, а не пакетный индекс: узел, впервые получивший такую работу, получает
её код вместе с ней, и между нами нет реестра, который надо поднимать,
авторизовывать и держать живым.

Нагрузок теперь две — стадия инференса и ранг Ray-кластера, — и обе ищутся
одинаково. Инференс здесь ничем не привилегирован: он просто первый жилец
этого механизма.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Optional


class PayloadMissing(RuntimeError):
    """Кода нагрузки рядом нет, и узлу нечего было бы запускать."""


def payload_dirs(name: str) -> Iterable[Path]:
    """Где может лежать нагрузка `name`, в порядке доверия.

    Мест два, и оба нужны. В контейнере нагрузка лежит там, куда её положил
    Dockerfile; в рабочем дереве — рядом с исходниками. Считать путь от
    `__file__` в одиночку нельзя: установленный пакет живёт в site-packages,
    где никакого `payloads/` рядом нет и не будет.
    """
    explicit = os.environ.get("LOOMA_PAYLOADS_DIR", "").strip()
    if explicit:
        yield Path(explicit) / name / name
        yield Path(explicit) / name
    here = Path(__file__).resolve()
    # /app/payloads/... в образе и <репозиторий>/payloads/... в разработке.
    for base in (Path("/app"), *here.parents[:5]):
        yield base / "payloads" / name / name


def collect(name: str, *, human: str, marker: str = "server.py",
            dirs: Optional[Iterable[Path]] = None) -> Dict[str, bytes]:
    """Собрать файлы нагрузки в то, что уедет задаче как её вход.

    `marker` — по чему опознаётся настоящий каталог: пустой или чужой каталог
    с подходящим именем встречается чаще, чем хотелось бы.
    """
    tried = []
    for candidate in (payload_dirs(name) if dirs is None else dirs):
        tried.append(candidate)
        if (candidate / marker).is_file():
            files = _package_files(candidate, name)
            if files:
                return files
    raise PayloadMissing(
        f"рядом с оркестратором нет {human}, и узлу нечего было бы запускать. "
        "Искали в: " + ", ".join(str(p) for p in tried) +
        ". В образе он кладётся Dockerfile'ом; путь можно задать через "
        "LOOMA_PAYLOADS_DIR"
    )


def _package_files(root: Path, name: str) -> Dict[str, bytes]:
    """`*.py` пакета вместе с подпакетами — путями относительно `root`.

    Подпакет — это каталог с `__init__.py`; всё прочее (`__pycache__`,
    тесты, окружения) остаётся. Со стенда: `looma_stage/train/` не уезжал,
    и стадия обучения падала на узле с `No module named 'looma_stage.train'`.
    """
    files: Dict[str, bytes] = {}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part.startswith((".", "__pycache__")) for part in relative.parts[:-1]):
            continue
        if not all((root / Path(*relative.parts[:depth]) / "__init__.py").is_file()
                   for depth in range(1, len(relative.parts))):
            continue
        files[f"{name}/{relative.as_posix()}"] = path.read_bytes()
    return files


def ray_payload() -> Dict[str, bytes]:
    """Файлы ранга Ray-кластера."""
    return collect("looma_ray", human="кода ранга Ray")
