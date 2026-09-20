"""Публичный прайс: классы карт, цены конкурентов, цены моделей за токен.

Это то, что видит лендинг и карточки в консоли, а заполняет — администратор.
Отдельно от ставок биллинга (usage/ledger.py) намеренно: ставка в аренде —
это то, по чему считается счёт, и она фиксируется в момент аренды; прайс —
это то, что обещано снаружи, и он может меняться сколько угодно без
последствий для уже открытых аренд.

Хранится одним JSON-документом в каталоге данных. Документ маленький, читают
его редко, а версии и «черновик/опубликовано» решаются тем, что админка
держит черновик у себя и присылает документ целиком.

Деньги — в копейках, целыми, как и везде.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class GpuClass:
    id: str
    name: str
    vendor: str = "NVIDIA"
    arch: str = ""
    vram_gb: int = 0
    logo_url: Optional[str] = None
    rate_kopecks: int = 0
    #: {"selectel": 31000, "aws": 48000} — за GPU-час, копейки
    competitors: Dict[str, int] = field(default_factory=dict)
    featured: bool = False
    #: Имена карт узлов, которые попадают в этот класс (подстрока gpu_name).
    match: List[str] = field(default_factory=list)


@dataclass
class ModelPrice:
    id: str
    context: int = 0
    price_in: int = 0      # копейки за 1M входных токенов
    price_out: int = 0     # копейки за 1M выходных токенов
    logo_url: Optional[str] = None
    visible: bool = True
    #: Репозиторий на HuggingFace — по владельцу подтягивается логотип.
    repo: str = ""


@dataclass
class Pricing:
    as_of: str = ""
    currency: str = "RUB"
    gpu_classes: List[GpuClass] = field(default_factory=list)
    models: List[ModelPrice] = field(default_factory=list)
    #: None — обучение по ставке кластера.
    training_rate_kopecks: Optional[int] = None
    #: Каких конкурентов показывать и как их подписывать.
    competitors: Dict[str, str] = field(default_factory=lambda: {"selectel": "Selectel", "aws": "AWS"})

    def as_dict(self) -> dict:
        return asdict(self)

    def public(self) -> dict:
        """То, что уходит без представления: скрытые модели не отдаём."""
        doc = self.as_dict()
        doc["models"] = [m for m in doc["models"] if m.get("visible", True)]
        return doc

    @classmethod
    def from_dict(cls, raw: dict) -> "Pricing":
        classes = [GpuClass(**_only(GpuClass, c)) for c in raw.get("gpu_classes") or []]
        models = [ModelPrice(**_only(ModelPrice, m)) for m in raw.get("models") or []]
        return cls(
            as_of=str(raw.get("as_of") or ""),
            currency=str(raw.get("currency") or "RUB"),
            gpu_classes=classes, models=models,
            training_rate_kopecks=_int_or_none(raw.get("training_rate_kopecks")),
            competitors=dict(raw.get("competitors") or {"selectel": "Selectel", "aws": "AWS"}),
        )

    def class_of(self, gpu_name: str) -> Optional[GpuClass]:
        """Класс по имени карты узла: «NVIDIA GeForce RTX 4090» → RTX 4090."""
        name = (gpu_name or "").lower()
        for c in self.gpu_classes:
            for needle in c.match or [c.name]:
                if needle.lower() in name:
                    return c
        return None


def _only(kind, raw: dict) -> dict:
    fields = kind.__dataclass_fields__
    out = {k: v for k, v in (raw or {}).items() if k in fields}
    for key in ("rate_kopecks", "price_in", "price_out", "vram_gb", "context"):
        if key in out and out[key] is not None:
            out[key] = int(out[key])
    if "competitors" in out:
        out["competitors"] = {str(k): int(v) for k, v in (out["competitors"] or {}).items()
                              if v not in (None, "")}
    return out


def _int_or_none(v: Any) -> Optional[int]:
    if v in (None, ""):
        return None
    return int(v)


class PricingStore:
    """Один документ на диске. Чтение — из памяти, запись — целиком и
    атомарно, чтобы полуписанный файл не стал прайсом."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._doc = Pricing()
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:
                self._doc = Pricing.from_dict(json.load(handle))
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as exc:      # битый файл — не роняем старт
            logger_warn(f"прайс {self.path} не разобран: {exc}; работаю с пустым")

    def read(self) -> Pricing:
        with self._lock:
            return self._doc

    def write(self, raw: dict) -> Pricing:
        doc = Pricing.from_dict(raw)
        if not doc.as_of:
            doc.as_of = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(doc.as_dict(), handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._doc = doc
        return doc


def logger_warn(text: str) -> None:
    import logging
    logging.getLogger("looma.pricing").warning(text)
