"""Адаптеры, которые дообучение оставило после себя.

Результат обучения лежит в результатах задачи-головы, а у задачи на узле
срок: через час несобранное убирается (`RETENTION_S` агента). Адаптер же
нужен потом — развернуть в инференс через день, через месяц. Поэтому
оркестратор забирает его к себе, как только обучение закончилось, и с этих
пор развёртывание берёт его отсюда, а не с узла.

Файлы, а не база: адаптер 8B при r=16 — под 200 МБ, и класть такое в
Postgres незачем. Каталог — `<data_dir>/adapters/<group_id>/`, внутри ровно
то, что понимают `peft` и vLLM: `adapter_config.json` и
`adapter_model.safetensors`, плюс `meta.json` с тем, от чего он.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

#: Что составляет адаптер PEFT. Ровно эти два имени — и у стадии, и у vLLM.
ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors")


class AdapterStore:
    def __init__(self, root) -> None:
        self.root = Path(root)

    def _dir(self, adapter_id: str) -> Path:
        if not adapter_id or "/" in adapter_id or adapter_id in (".", ".."):
            raise ValueError(f"плохой идентификатор адаптера: {adapter_id!r}")
        return self.root / adapter_id

    def has(self, adapter_id: str) -> bool:
        where = self._dir(adapter_id)
        return all((where / name).is_file() for name in ADAPTER_FILES)

    def save(self, adapter_id: str, files: Dict[str, bytes], *, meta: dict) -> Path:
        """Положить адаптер. Атомарно по каталогу: пока не дописан — не виден."""
        missing = [name for name in ADAPTER_FILES if name not in files]
        if missing:
            raise ValueError(f"адаптер без {missing}")
        final = self._dir(adapter_id)
        staging = final.with_name(final.name + ".part")
        if staging.exists():
            for leftover in staging.iterdir():
                leftover.unlink()
        staging.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (staging / name).write_bytes(data)
        (staging / "meta.json").write_text(json.dumps(
            {**meta, "saved_at": time.time(),
             "bytes": sum(len(d) for d in files.values())}, ensure_ascii=False))
        if final.exists():
            for leftover in final.iterdir():
                leftover.unlink()
            final.rmdir()
        os.replace(staging, final)
        return final

    def files(self, adapter_id: str) -> Dict[str, bytes]:
        where = self._dir(adapter_id)
        if not self.has(adapter_id):
            raise FileNotFoundError(f"адаптера {adapter_id} нет")
        return {name: (where / name).read_bytes() for name in ADAPTER_FILES}

    def meta(self, adapter_id: str) -> Optional[dict]:
        where = self._dir(adapter_id) / "meta.json"
        if not where.is_file():
            return None
        try:
            return json.loads(where.read_text())
        except ValueError:
            return None

    def list(self) -> List[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir()
                      if p.is_dir() and not p.name.endswith(".part") and self.has(p.name))


def adapter_root(config) -> Path:
    """Где хранить: рядом с остальными данными оркестратора. Без настройки
    (тесты, оркестратор без каталога) — во временном каталоге процесса:
    работает, но не переживает перезапуск, и об этом сказано в логе."""
    import logging
    import tempfile

    data_dir = getattr(config, "data_dir", None)
    if data_dir:
        return Path(data_dir) / "adapters"
    made = Path(tempfile.mkdtemp(prefix="looma-adapters-"))
    logging.getLogger(__name__).warning(
        "каталог данных не задан: адаптеры лягут в %s и не переживут перезапуск", made)
    return made
