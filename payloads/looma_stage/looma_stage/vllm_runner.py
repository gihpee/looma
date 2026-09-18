# Adapted from Parallax (https://github.com/GradientHQ/parallax, arXiv:2509.26182).
# Original: src/parallax/vllm/model_runner.py — ParallaxVLLMGroupCoordinator
# (ответ «первый/последний ранг» по диапазону слоёв вместо номера процесса),
# ParallaxVLLMModelRunner.load_model (подмена get_pp_indices на время загрузки)
# и initialize_vllm_model_runner (порядок поднятия).
# Изменения: решения вынесены в чистые функции и проверяются без vLLM и без
# карты; подмена get_pp_indices возвращается на место через try/finally в любом
# случае; отказы называют причину вместо предупреждения в лог и продолжения с
# наполовину настроенным движком.
"""Заставить vLLM собрать только слои этой стадии.

Ключ ко всему — то, что конвейер у vLLM **уже есть**. Он спрашивает у себя две
вещи, и обе можно ответить по-своему:

    get_pp_indices(...)      какие слои строит этот ранг
    pp_group.is_first_rank   строить ли embed_tokens
    pp_group.is_last_rank    строить ли lm_head

Обычно на них отвечает номер процесса в распределённой группе. Мы отвечаем
**диапазоном слоёв** — и модель собирается срезом, без единой строчки про
конкретную архитектуру. Именно поэтому здесь нет и не будет файлов вида
`qwen3.py`: слои строит сам vLLM, мы только говорим ему, какие.

Распределённая группа при этом настоящая — по процессу на каждую карту
узла, NCCL между ними, каждый слой порезан поровну (tensor parallelism). Но
это группа ВНУТРИ машины: обмен между стадиями через неё не идёт, он идёт
через агента. Всё отсюда накладывается в каждом воркере (см. vllm_worker.py).
"""

from __future__ import annotations

import contextlib
import logging
from typing import Iterator, Tuple

logger = logging.getLogger("looma_stage.vllm_runner")


class RunnerRefused(RuntimeError):
    """Собрать движок тут нельзя, и вот почему."""


# ------------------------------------------------------------------ решения
def stage_role(start_layer: int, end_layer: int, num_layers: int) -> Tuple[bool, bool]:
    """Первая ли это стадия и последняя ли. От этого зависит, что вообще
    строится: эмбеддинги у первой, голова у последней."""
    if num_layers <= 0:
        raise RunnerRefused("в конфиге модели нет числа слоёв")
    if not 0 <= start_layer < end_layer <= num_layers:
        raise RunnerRefused(
            f"срез [{start_layer}, {end_layer}) не помещается в модель "
            f"из {num_layers} слоёв")
    return start_layer == 0, end_layer == num_layers


@contextlib.contextmanager
def layer_range(start_layer: int, end_layer: int) -> Iterator[None]:
    """На время загрузки: сколько бы слоёв vLLM ни насчитал, строит он наши.

    Через try/finally, и это не аккуратность: подменённая функция — глобальная,
    и оставить её после неудачной загрузки значит испортить всё, что попробует
    грузить модель после нас, включая сообщение об ошибке.
    """
    try:
        import vllm.distributed.utils as utils
    except ImportError as exc:
        raise RunnerRefused(
            f"внутренности vLLM недоступны ({exc}); стадия рассчитана на "
            "версию, закреплённую в требованиях") from None

    original = utils.get_pp_indices

    def ours(num_layers: int, rank: int, world_size: int):
        return start_layer, end_layer

    utils.get_pp_indices = ours
    try:
        yield
    finally:
        utils.get_pp_indices = original


def coordinator_for(start_layer: int, end_layer: int, num_layers: int):
    """Группа, которая отвечает про первый и последний ранг по слоям.

    Класс собирается внутри функции: его базовый тип живёт в vLLM, которого на
    машине без карты нет вовсе, а модуль обязан импортироваться и там.
    """
    from vllm.distributed.parallel_state import GroupCoordinator

    is_first, is_last = stage_role(start_layer, end_layer, num_layers)

    class StageGroupCoordinator(GroupCoordinator):
        @property
        def is_first_rank(self) -> bool:
            return is_first

        @property
        def is_last_rank(self) -> bool:
            return is_last

    return StageGroupCoordinator


def stage_runner_class(start_layer: int, end_layer: int, num_layers: int):
    """Исполнитель vLLM, знающий свой срез.

    Собирается внутри функции по той же причине, что и координатор: базовый
    тип живёт в vLLM, а модуль обязан импортироваться и там, где его нет.

    Добавляет к штатному ровно одно — шаг, умеющий принять промежуточные
    тензоры снаружи. Кэш под наши слои раскладывает драйвер на все воркеры
    сразу (`lay_out_cache`), а не каждый исполнитель себе.
    """
    from vllm.v1.worker.gpu_model_runner import GPUModelRunner

    is_first, is_last = stage_role(start_layer, end_layer, num_layers)

    class StageRunner(GPUModelRunner):
        start_layer_index = start_layer
        end_layer_index = end_layer
        is_first_stage = is_first
        is_last_stage = is_last

        # --------------------------------------------------------- шаг
        def execute_model(self, scheduler_output, intermediate_tensors=None,
                          *args, **kwargs):
            """Шаг модели. Перед ним — буфер под входящие тензоры.

            vLLM не принимает их напрямую: он копирует пришедшее в СВОЙ буфер
            и нарезает по размеру батча. Буфера у неголовной стадии нет, пока
            его не завели, и шаг падает на

                assert self.intermediate_tensors is not None

            — утверждении, из которого не следует, что кто-то должен был этот
            буфер выделить.
            """
            if not self.is_first_stage:
                self._ensure_incoming()
            return super().execute_model(scheduler_output, intermediate_tensors,
                                         *args, **kwargs)

        def _ensure_incoming(self):
            """Завести буфер один раз и переиспользовать.

            Он размером с самый большой батч, и создавать его на каждом шаге
            значило бы выделять гигабайты в горячем пути.
            """
            if getattr(self, "intermediate_tensors", None) is not None:
                return
            self.intermediate_tensors = self.model.make_empty_intermediate_tensors(
                batch_size=self.max_num_tokens,
                dtype=self.model_config.dtype, device=self.device)
            logger.info("буфер под входящие тензоры заведён")

    return StageRunner


def lay_out_cache(executor, config, *, block_size: int, max_model_len: int):
    """Завести KV-кэш под наши слои на всех воркерах и вернуть менеджер блоков.

    У кэша две половины, и путать их нельзя.

    РАБОЧАЯ живёт в воркерах: она выделяет сами тензоры и связывает их со
    слоями внимания, попутно собирая attn_groups. Без неё модель грузится,
    кэш «есть», а первый же шаг падает на
        IndexError: list index out of range
    в attn_groups[0] — и по этому сообщению не догадаться, что пропущен целый
    шаг инициализации. Заводится штатным `initialize_from_config`.

    ПЛАНИРОВЩИКОВАЯ — это менеджер блоков: он решает, кому какие блоки
    выдать, и именно его зовёт наша сборка батча. Он один, в драйвере: состав
    батча один на все карты, и блоки под него обязаны совпасть на всех.

    Спецификацию кэша спрашиваем у самих воркеров, а не считаем по конфигу:
    сколько голов достаётся карте при tensor parallelism — знает модель, а
    угадать это по конфигу значит однажды угадать неверно и получить кэш не
    той формы. Такая ошибка не падает, она портит внимание.

    Места берём наименьшее по картам: раскладка обязана быть одной на всех,
    и карта, где свободно меньше, задаёт её всем.
    """
    from vllm.v1.core.kv_cache_manager import KVCacheManager
    from vllm.v1.core.kv_cache_utils import (generate_scheduler_kv_cache_config,
                                             get_kv_cache_configs)

    specs = executor.collective_rpc("get_kv_cache_spec")
    room = executor.collective_rpc("stage_cache_room")
    if not specs or not room:
        raise RunnerRefused("ни один воркер не рассказал про KV-кэш")
    least = min(int(size) for size in room)
    if len(room) > 1 and least < max(room):
        logger.info("под кэш берём %.1f ГБ — столько свободно на самой занятой "
                    "карте (на самой свободной %.1f ГБ)",
                    least / 1024 ** 3, max(room) / 1024 ** 3)

    configs = get_kv_cache_configs(vllm_config=config, kv_cache_specs=specs,
                                   available_memory=[least] * len(specs))
    executor.collective_rpc("initialize_from_config", args=(configs,))
    # Ядра под настоящие формы — сейчас, на всех стадиях сразу, а не на
    # первом запросе по очереди через весь конвейер.
    executor.collective_rpc("stage_warm_up")

    scheduler_config = generate_scheduler_kv_cache_config(configs)
    manager = KVCacheManager(
        kv_cache_config=scheduler_config, max_model_len=max_model_len,
        enable_caching=False, use_eagle=False, log_stats=False,
        enable_kv_cache_events=False, dcp_world_size=1,
        hash_block_size=block_size)
    logger.info("кэш разложен на %d воркерах: блоков %s", len(specs),
                getattr(scheduler_config, "num_blocks", "?"))
    return scheduler_config, manager


def replace_pipeline_group(start_layer: int, end_layer: int, num_layers: int) -> None:
    """Подменить группу конвейера на ту, что считает по слоям.

    Отдельным шагом после `initialize_model_parallel`: vLLM собирает свою
    группу сам, и переопределить в ней два свойства проще, чем построить свою
    с нуля со всем, что к ней прилагается.

    Подменяется КЛАСС уже собранной группы, а не строится вторая. Строить
    вторую значило бы звать `torch.distributed.new_group`, а это коллективная
    операция: её обязаны позвать все процессы узла с одним и тем же списком
    групп. При tensor parallelism у каждого воркера своя группа конвейера из
    него одного, списки разные — и такой вызов повис бы на первом же узле с
    двумя картами. У подмены класса коллективов нет: свойства меняются у
    объекта, который у каждого свой.
    """
    from vllm.distributed import parallel_state

    existing = parallel_state._PP
    if existing is None:
        raise RunnerRefused(
            "vLLM не поднял группу конвейера; порядок инициализации нарушен")

    existing.__class__ = coordinator_for(start_layer, end_layer, num_layers)
    logger.info("группа конвейера считает по слоям: первая=%s, последняя=%s",
                existing.is_first_rank, existing.is_last_rank)
