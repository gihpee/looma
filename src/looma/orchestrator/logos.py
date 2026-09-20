"""Логотипы моделей — с HuggingFace, а не из кода.

У каждой модели на HF есть владелец (организация или человек), а у владельца —
аватар. Это и есть логотип модели, который ожидают увидеть в карточке:
Qwen — у Qwen, Llama — у meta-llama. Хардкодить их значило бы заводить
картинку на каждую новую модель руками; здесь они подтягиваются по имени
репозитория, а администратор может переопределить ссылку в прайсе.

Сеть — не обязательна: без ответа HF остаётся плашка с инициалами, и это
законное состояние, а не ошибка. Ответы кэшируются в памяти: у одного
владельца десятки моделей, а аватар один.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from typing import Dict, Optional, Tuple

logger = logging.getLogger("looma.logos")

HF = "https://huggingface.co/api"
TIMEOUT_S = 4.0
#: Сколько помнить ответ (и отсутствие ответа).
CACHE_S = 24 * 3600.0

_cache: Dict[str, Tuple[float, Optional[str]]] = {}


def owner_of(repo: str) -> str:
    """`Qwen/Qwen3-8B` → `Qwen`; имя без косой черты — владельца нет."""
    repo = (repo or "").strip().strip("/")
    return repo.split("/", 1)[0] if "/" in repo else ""


def _fetch(owner: str, opener=None) -> Optional[str]:
    """Синхронно: сначала как организация, потом как человек. 404 в обоих
    случаях — владельца нет или аватар не задан; это не ошибка."""
    open_url = opener or urllib.request.urlopen
    for kind in ("organizations", "users"):
        try:
            with open_url(f"{HF}/{kind}/{owner}/avatar", timeout=TIMEOUT_S) as reply:
                url = (json.loads(reply.read() or b"{}") or {}).get("avatarUrl")
                if url:
                    return str(url)
        except Exception as exc:   # 404, сеть, таймаут — всё одинаково «нет»
            if getattr(exc, "code", None) not in (404,):
                logger.debug("аватар %s/%s: %s", kind, owner, exc)
    return None


def cached(owner: str) -> Tuple[bool, Optional[str]]:
    """Есть ли в кэше и что там. Отдельно от загрузки, чтобы ответ клиенту
    никогда не ждал сети: не знаем — отдаём пусто и узнаём в фоне."""
    hit = _cache.get(owner)
    if hit is None or time.time() - hit[0] > CACHE_S:
        return False, None
    return True, hit[1]


async def avatar(repo: str, *, opener=None) -> Optional[str]:
    """Ссылка на аватар владельца репозитория или None."""
    owner = owner_of(repo)
    if not owner:
        return None
    known, url = cached(owner)
    if known:
        return url
    url = await asyncio.to_thread(_fetch, owner, opener)
    _cache[owner] = (time.time(), url)
    return url


_inflight: set = set()


def peek(repo: str) -> Optional[str]:
    """Что уже известно, без ожидания: в списке моделей ответ не должен ждать
    HF. Неизвестное запрашивается в фоне и появится при следующем запросе."""
    owner = owner_of(repo)
    if not owner:
        return None
    known, url = cached(owner)
    if known:
        return url
    if owner not in _inflight:
        _inflight.add(owner)

        async def fetch() -> None:
            try:
                await avatar(repo)
            finally:
                _inflight.discard(owner)

        try:
            asyncio.get_running_loop().create_task(fetch())
        except RuntimeError:
            _inflight.discard(owner)
    return None


def forget() -> None:
    _cache.clear()
