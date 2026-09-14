# Adapted from Parallax (https://github.com/GradientHQ/parallax, arXiv:2509.26182).
# Original: src/parallax/vllm/model_runner.py — initialize_vllm_model_runner
# (порядок поднятия: заплаты, распределённая группа, подмена группы конвейера,
# конфиги, загрузка) и ParallaxVLLMModelRunner.load_model.
# Изменения: без LoRA, MoE-роутинга и спекулятивного декодирования — их тут
# нечем проверить и незачем нести; поднятие разбито на именованные шаги, чтобы
# отказ называл, на каком именно; проверка карты до всего остального.
"""Движок vLLM, собирающий только слои этой стадии — на всех картах узла.

Как это устроено. Между машинами — pipeline parallelism: стадия держит свой
диапазон слоёв, активации ходят через агента. Внутри машины — tensor
parallelism: стадия поднимается через штатный исполнитель vLLM с
`tensor_parallel_size = число видимых карт`, по воркеру на карту, NCCL между
ними по PCIe/NVLink, каждый слой порезан на все карты поровну. Одна карта —
тот же путь с одним воркером.

Этот процесс — ДРАЙВЕР: карту он не держит. Он собирает батч (менеджер
блоков KV-кэша живёт здесь, один на все карты), рассылает его воркерам и
забирает результат с нулевого. Всё, что считает, — в `vllm_worker.py`.

Почему не по агенту на карту с обменом активациями через сеть: карты одной
машины связаны шиной в сотни гигабит, а сосед по конвейеру — тоннелем в
десятки мегабит. Гонять через тоннель то, что можно сложить по шине, —
терять на каждом токене.

Приём держится на трёх вмешательствах во внутренности vLLM (см.
vllm_runner.py и vllm_patch.py), и любое из них может разойтись с версией
движка. Узнать это на загрузке — минуты; узнать на батче, проделав всю
работу, — недели.

Проверить на узле, ничего не разворачивая:

    python -m looma_stage.vllm_engine --weights Qwen/Qwen3-4B \\
        --start-layer 0 --end-layer 18 --num-model-layers 36
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import threading

from looma_stage.vllm_runner import RunnerRefused, lay_out_cache, stage_role

logger = logging.getLogger("looma_stage.vllm_engine")

# Конфиг vLLM, установленный на всю жизнь процесса.
#
# У него есть глобальный «текущий конфиг», и части движка спрашивают его сами,
# без всяких аргументов: бэкенды внимания, CustomOp'ы. Вне контекста это падает
#     AssertionError: Current vLLM config is not set
# из места, которое к конфигу отношения не имеет — например, из раскладки
# KV-кэша.
#
# Держим открытым, а не оборачиваем каждый вызов: стадия в процессе одна и
# живёт столько же, сколько процесс, а забыть обернуть один вызов из десяти —
# ровно тот способ получить это исключение через неделю.
_CONFIG = None

# Потолок доли карты. Не размер запроса, а именно потолок: выше него vLLM не
# оставляет места под собственные буферы, а на чужом узле рядом живёт ещё
# кто-то — вторая стадия того же кластера, например.
MAX_UTILISATION = 0.9

# Запас поверх весов и кэша: активации шага, буферы связи, фрагментация. Число
# грубое, и точнее его сделать нельзя — оно зависит от длины батча, которую мы
# узнаем только в работе. Занизить его хуже, чем завысить: нехватка вылезет
# посреди запроса, а не при загрузке.
OVERHEAD_BYTES = 2 * 1024 ** 3

# Сколько ждать один шаг от воркеров. Без предела зависшая NCCL-коллектива
# (одна карта отвалилась, остальные ждут её вечно) вешала бы стадию молча;
# с пределом шаг падает с причиной, голова узнаёт и снимает батч.
STEP_TIMEOUT_S = 600


def dtype_bytes(dtype: str) -> int:
    """Байт на число. Неизвестное имя — отказ, а не догадка: ошибка вдвое даёт
    вдвое неверный размер кэша, и узел либо не поднимется, либо пообещает
    вдвое больше, чем сможет."""
    known = {"float16": 2, "fp16": 2, "half": 2, "bfloat16": 2, "bf16": 2,
             "float32": 4, "fp32": 4, "float": 4,
             "float8": 1, "fp8": 1, "int8": 1}
    size = known.get((dtype or "").strip().lower())
    if size is None:
        raise RunnerRefused(
            f"не знаю, сколько байт занимает {dtype!r}; посчитать размер "
            f"KV-кэша нечем. Известные: {', '.join(sorted(known))}")
    return size


def kv_bytes_per_token(config, *, layers: int, dtype: str) -> int:
    """Сколько байт KV-кэша стоит один токен на ЭТОЙ стадии.

    Ключи и значения, на каждый слой среза, по числу KV-голов. Голов именно
    KV, а не внимания: у моделей с GQA их в несколько раз меньше, и считать по
    головам внимания значило бы завысить кэш вчетверо и не поднять стадию там,
    где памяти хватало.
    """
    heads = (getattr(config, "num_key_value_heads", None)
             or getattr(config, "num_attention_heads", None))
    attention_heads = getattr(config, "num_attention_heads", None) or 0
    hidden = getattr(config, "hidden_size", None) or 0
    head_dim = getattr(config, "head_dim", None) or (
        hidden // attention_heads if attention_heads else 0)
    if not heads or not head_dim:
        raise RunnerRefused(
            "в конфиге модели нет числа KV-голов или размера головы "
            f"(num_key_value_heads={heads}, head_dim={head_dim}); посчитать "
            "размер кэша нечем")
    # 2 — ключи и значения.
    return 2 * int(heads) * int(head_dim) * dtype_bytes(dtype) * max(0, layers)


@dataclass
class Plan:
    """Сколько карты просить и что за это обещано."""

    utilisation: float
    max_sequences: int
    bytes_needed: int
    #: Почему получилось столько — уходит в лог, чтобы число не выглядело
    #: взявшимся ниоткуда.
    why: str


def plan_memory(*, per_token: int, weights_bytes: int, total_bytes: int,
                budget_bytes: int, max_sequences: int,
                max_model_len: int) -> Plan:
    """Сколько карты нужно под обещанную ёмкость — и что делать, если столько нет.

    Считаем от обещания: `max_sequences` последовательностей по
    `max_model_len` токенов каждая. Это верхняя граница, и брать её надо
    целиком: кэш выделяется заранее, и «в среднем хватит» здесь означает, что
    в худшем случае запрос упрётся в нехватку блоков посреди ответа.

    Не влезает — уменьшаем обещание, а не берём меньше кэша под то же число.
    Иначе узел принимал бы запросы, которые не может досчитать: голова видит
    свой потолок, а блоки кончаются у стадии, и заметно это станет уже в
    работе.
    """
    budget = max(0, min(budget_bytes, int(total_bytes * MAX_UTILISATION)))
    for_cache = budget - weights_bytes - OVERHEAD_BYTES
    per_sequence = per_token * max(1, max_model_len)
    if for_cache < per_sequence:
        raise RunnerRefused(
            f"на карте нет места даже под одну последовательность: под кэш "
            f"остаётся {for_cache / 1024**3:.1f} ГБ, а одна длиной "
            f"{max_model_len} требует {per_sequence / 1024**3:.1f} ГБ. "
            "Уменьшите длину контекста или дайте стадии меньше слоёв")

    fits = min(max_sequences, for_cache // per_sequence)
    needed = weights_bytes + OVERHEAD_BYTES + fits * per_sequence
    utilisation = min(MAX_UTILISATION, needed / max(1, total_bytes))
    why = (f"{fits} последовательностей по {max_model_len} токенов = "
           f"{fits * per_sequence / 1024**3:.1f} ГБ кэша, веса "
           f"{weights_bytes / 1024**3:.1f} ГБ, запас "
           f"{OVERHEAD_BYTES / 1024**3:.1f} ГБ — итого "
           f"{needed / 1024**3:.1f} ГБ из {total_bytes / 1024**3:.1f}")
    if fits < max_sequences:
        why += (f"; просили {max_sequences}, но столько не помещается — "
                "узел обещает меньше, а не берёт кэш меньше обещанного")
    return Plan(utilisation=utilisation, max_sequences=int(fits),
                bytes_needed=int(needed), why=why)


def kv_heads(config) -> int:
    """Сколько KV-голов у модели — по стольким карта делит кэш при TP."""
    return int(getattr(config, "num_key_value_heads", None)
               or getattr(config, "num_attention_heads", None) or 1)


def per_card(total: int, cards: int, *, heads: int = 0) -> int:
    """Сколько из общего достаётся одной карте при tensor parallelism.

    Веса делятся на все карты. KV-кэш — по головам: когда карт больше, чем
    KV-голов (GQA-модель на восьми картах), vLLM головы дублирует, и на карту
    приходится 1/heads, а не 1/cards. Считать иначе значило бы обещать кэш,
    которого на карте нет.
    """
    cards = max(1, int(cards))
    share = min(cards, heads) if heads > 0 else cards
    return -(-int(total) // share)


def plan_for_shard(model_path: str, *, layers: int, dtype: str,
                   vram_quota_bytes: int, max_sequences: int,
                   max_model_len: int, cards: int = 1) -> "Plan":
    """Сколько попросит эта стадия — от КАЖДОЙ карты.

    Считается от того, что она обещает обслужить, а не берётся долей наугад.
    vLLM выделяет KV-кэш заранее и на всю отведённую долю, так что
    фиксированные «70% карты» означали бы, что стадия крошечной модели
    занимает столько же, сколько огромной, и соседу на этом узле места не
    остаётся.

    При нескольких картах и веса, и кэш режутся между ними, а квота узла
    (оркестратор считает её как `карт × меньшая карта`) — поровну на карту.
    Доля одна на все карты: vLLM применяет её к каждой, поэтому считается
    она от самой маленькой из них.
    """
    from transformers import AutoConfig

    config = AutoConfig.from_pretrained(model_path)
    total = card_bytes(cards)
    quota = vram_quota_bytes // cards if vram_quota_bytes > 0 else total
    return plan_memory(
        per_token=per_card(kv_bytes_per_token(config, layers=layers, dtype=dtype),
                           cards, heads=kv_heads(config)),
        weights_bytes=per_card(weights_size(model_path), cards),
        total_bytes=total, budget_bytes=quota,
        max_sequences=max_sequences, max_model_len=max_model_len)


def card_count() -> int:
    """Сколько карт видит этот процесс — столько и воркеров.

    Агент отдаёт задаче её карты через CUDA_VISIBLE_DEVICES, так что «все
    видимые» — это ровно те, что выданы, а не все, что есть на машине.
    """
    import torch

    return max(1, int(torch.cuda.device_count()))


def card_bytes(cards: int = 1) -> int:
    """Память самой маленькой из карт: доля vLLM одна на все, и считать её
    надо от той, где меньше всего. Отдельной функцией — чтобы расчёт можно
    было проверить там, где карты нет."""
    import torch

    return min(int(torch.cuda.get_device_properties(index).total_memory)
               for index in range(max(1, int(cards))))


def weights_size(model_path: str) -> int:
    """Сколько весит срез на диске. Оценка весов в памяти — по файлам, которые
    стадия и будет читать: они уже урезаны до её слоёв."""
    import os

    total = 0
    try:
        for name in os.listdir(model_path):
            if name.endswith((".safetensors", ".bin")):
                total += os.path.getsize(os.path.join(model_path, name))
    except OSError:
        return 0
    return total


@dataclass
class LoadedShard:
    """Что получилось загрузить. Возвращается, чтобы это можно было показать
    и сравнить с тем, что просили."""

    start_layer: int
    end_layer: int
    num_layers: int
    is_first: bool
    is_last: bool
    dtype: str
    #: Драйвер (`StageDriver`): менеджер блоков и связь с воркерами. Карты у
    #: него нет — считают воркеры.
    runner: object
    #: Сколько последовательностей стадия реально может держать — после того,
    #: как под них нашлось место. Может быть меньше запрошенного.
    max_sequences: int = 0
    #: На скольких картах, то есть сколько воркеров режут каждый слой.
    cards: int = 1

    def as_dict(self) -> dict:
        return {
            "layers": [self.start_layer, self.end_layer],
            "of": self.num_layers,
            "first": self.is_first,
            "last": self.is_last,
            "dtype": self.dtype,
            "мест": self.max_sequences,
            "карт": self.cards,
        }

    def close(self) -> None:
        close = getattr(self.runner, "close", None)
        if close is not None:
            close()


def require_cuda() -> None:
    """Отказать до того, как что-то поднято.

    vLLM без карты не работает, и выясняется это глубоко внутри — сообщением,
    по которому не видно, что дело в железе, а не в модели или срезе.
    """
    try:
        import torch
    except ImportError as exc:
        raise RunnerRefused(f"нет torch ({exc})") from None
    if not torch.cuda.is_available():
        raise RunnerRefused(
            "vLLM работает только на CUDA, а карты на этом узле не видно. "
            "Для CPU и Apple есть движок torch — он медленнее и не умеет "
            "батчить, но считает везде")


def _config_with(kind, **options):
    """Собрать конфиг vLLM, отбросив поля, которых в этой версии нет.

    Поля конфигов переезжают между версиями, и лишний аргумент роняет всё
    поднятие целиком — сообщением про имя, а не про то, что версия другая.
    Отброшенное называется вслух: молча потерянный `enforce_eager` вернул бы
    захват графов и падение внутри него.
    """
    import dataclasses

    try:
        known = {field.name for field in dataclasses.fields(kind)}
    except TypeError:
        return kind(**options)
    dropped = sorted(set(options) - known)
    if dropped:
        logger.warning("%s не знает про %s — эта версия vLLM устроена иначе",
                       kind.__name__, ", ".join(dropped))
    return kind(**{name: value for name, value in options.items() if name in known})


def prepare_weights(weights: str, *, start_layer: int, end_layer: int,
                    is_first: bool, is_last: bool, dtype: str) -> str:
    """Положить рядом ровно те веса, которые нужны этой стадии.

    Две разные экономии, и обе заметные:

    **Скачивание.** Из репозитория берутся метаданные, а по ним — только те
    файлы safetensors, где лежат наши слои. Половина модели вместо целой.

    **Чтение.** vLLM, в отличие от нашего исполнителя, не терпит неполного
    чекпоинта: он перечисляет каждый файл из `model.safetensors.index.json` и
    открывает его, так что недостающий — ошибка, а не экономия. Поэтому рядом
    собирается «вид»: симлинки на нужные файлы плюс переписанный индекс, где
    упомянуты только они.

    Если урезать нечего — единственный файл, незнакомые имена ключей — вернётся
    исходный путь. Это не отказ: стадия просто прочитает больше, чем ей нужно.
    """
    from looma_stage.loader import ShardSpec, build_stage_checkpoint_view, resolve_model_path

    spec = ShardSpec(model_path=weights, start_layer=start_layer,
                     end_layer=end_layer, is_first=is_first, is_last=is_last,
                     dtype=dtype)
    local = resolve_model_path(weights, shard=spec)
    view = build_stage_checkpoint_view(local, spec)
    if view != local:
        logger.info("читаем урезанный чекпоинт: %s", view)
    else:
        logger.info("чекпоинт урезать нечем, читаем целиком: %s", local)
    return view


def _build_config(model_path: str, *, dtype: str, max_model_len: int,
                  utilisation: float, block_size: int, max_sequences: int,
                  max_batched_tokens: int, cards: int, stage: dict):
    """Конфиги vLLM. Всё, чего мы не используем, названо явно нулём или None —
    молчаливое умолчание тут означало бы «как получится».

    `stage` — срез этой стадии; уезжает воркерам в `additional_config`, потому
    что конфиг — единственное, что vLLM передаёт в процесс воркера при его
    создании.
    """
    from vllm.config import (CacheConfig, DeviceConfig, LoadConfig, ModelConfig,
                             ParallelConfig, SchedulerConfig, VllmConfig)

    from looma_stage import vllm_worker

    # Без torch.compile и без захвата CUDA-графов.
    #
    # Штатный движок перед захватом делает прогревочные прогоны и компилирует
    # всё заранее. Мы правим исполнителем напрямую, прогрева не делаем — и
    # первый же настоящий шаг запускает компиляцию ВНУТРИ захвата графа, где
    # нельзя даже прочитать состояние генератора:
    #
    #   RuntimeError: Cannot call CUDAGeneratorImpl::current_seed during
    #   CUDA graph capture
    #
    # Захват тут и не нужен: он рассчитан на формы батчей, которые выбирает
    # сам vLLM, а у нас их выбирает первая стадия. Плата — eager вместо
    # скомпилированного, то есть медленнее на шаг; вернуть это можно, добавив
    # честный прогрев, но сначала конвейер должен просто заработать.
    model = _config_with(ModelConfig,
        model=model_path, tokenizer=model_path, tokenizer_mode="auto",
        trust_remote_code=True, dtype=dtype, seed=0,
        max_model_len=max_model_len, max_logprobs=1, enforce_eager=True,
    )
    # Исполнитель "mp" и при одной карте: путь один на все узлы, и узел с
    # одной картой проверяет тот же код, что и узел с четырьмя. Конвейер
    # для vLLM всегда из одной стадии — между машинами он наш, не его.
    parallel = _config_with(ParallelConfig,
        pipeline_parallel_size=1, tensor_parallel_size=int(cards),
        distributed_executor_backend="mp", worker_cls=vllm_worker.QUALNAME,
    )
    return VllmConfig(
        model_config=model,
        cache_config=CacheConfig(block_size=block_size,
                                 gpu_memory_utilization=utilisation,
                                 swap_space=0, cache_dtype="auto"),
        parallel_config=parallel,
        scheduler_config=SchedulerConfig(
            max_num_batched_tokens=max(max_batched_tokens, model.max_model_len),
            max_num_seqs=max_sequences, max_model_len=model.max_model_len,
            is_encoder_decoder=False, enable_chunked_prefill=False),
        # Без номера: каждый воркер берёт карту по своему рангу.
        device_config=DeviceConfig(device="cuda"),
        load_config=LoadConfig(load_format="auto"),
        lora_config=None, speculative_config=None, quant_config=None,
        kv_transfer_config=None, kv_events_config=None,
        additional_config={vllm_worker.SETTINGS_KEY: dict(stage)},
    )


def forbid_compile() -> None:
    """Не давать torch.compile'у собирать ядра: на узле нет компилятора.

    Со стенда (nv3, 2 карты): модель поднялась, а первый шаг упал с
        Failed to find C compiler. Please specify via CC environment variable
    из недр Triton. Виновник — `VocabParallelEmbedding.forward` в vLLM: при
    `tp_size > 1` он зовёт `get_masked_input_and_mask`, обёрнутую в
    `@torch.compile(backend="inductor")`. Inductor генерирует Triton-ядро,
    Triton при первом запуске компилирует свой C-модуль — и ему нужен `cc`.
    На одной карте ветка не выполняется, поэтому раньше это не всплывало.

    Функция чисто поэлементная и в eager считается так же, только без
    слияния в одно ядро: одна операция на шаг над индексами токенов. Ставить
    ради неё gcc в образ агента — плюс полторы сотни мегабайт на каждый узел.

    Переменную читает `torch._dynamo.config` при импорте, поэтому ставится
    ДО подъёма воркеров — они наследуют окружение. В самом воркере флаг
    дублируется прямо в конфиге (см. vllm_worker): на случай, если torch там
    уже импортирован к моменту, когда до этого дошло.
    """
    import os

    os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")


def start_executor(config):
    """Поднять воркеры — по одному на карту — через исполнитель vLLM.

    Отказ воркера приходит сюда одной общей фразой («initialization failed…
    see stack trace»), а причина — в логе самого воркера выше по выводу: он
    пишет в тот же stderr. Поэтому отказ здесь называет, куда смотреть.
    """
    from vllm.v1.executor.abstract import Executor

    try:
        return Executor.get_class(config)(config)
    except Exception as exc:
        raise RunnerRefused(
            f"воркеры vLLM не поднялись ({exc}); причина — в их логе выше, "
            "строки с «WorkerProc failed»") from exc


def shm_room() -> int:
    """Сколько свободно в /dev/shm — или -1, если его тут нет."""
    import os

    try:
        stat = os.statvfs("/dev/shm")
    except OSError:
        return -1
    return int(stat.f_bavail * stat.f_frsize)


def shm_needed(cards: int) -> int:
    """Сколько разделяемой памяти могут занять очереди исполнителя.

    Очередь драйвер→воркеры — кольцо из 10 кусков по VLLM_MQ_MAX_CHUNK_BYTES_MB
    (16 МБ), ответная очередь каждого воркера — 10 по 24 МБ. Кольца
    заполняются по мере работы, а не при создании, так что это верхняя
    граница, а не то, что займётся сразу.
    """
    import os

    chunk = int(os.environ.get("VLLM_MQ_MAX_CHUNK_BYTES_MB", "16")) * 1024 ** 2
    return 10 * chunk + max(1, int(cards)) * 10 * 24 * 1024 ** 2


def warn_if_shm_tight(cards: int) -> None:
    """Сказать заранее, если /dev/shm мал.

    Очереди исполнителя живут в /dev/shm, а в контейнере он по умолчанию
    64 МБ. Кончится он не при создании, а при первой записи за край —
    воркер умрёт по SIGBUS без единой строки в логе. Здесь это не отказ:
    мелкие батчи в такой объём укладываются, а насколько крупные пойдут —
    заранее не известно. Но пусть в логе будет, на что смотреть, когда
    воркер исчезнет молча.
    """
    room, needed = shm_room(), shm_needed(cards)
    if room < 0 or room >= needed:
        return
    logger.warning(
        "в /dev/shm свободно %d МБ, а очереди исполнителя vLLM на %d карт(ы) "
        "могут занять до %d МБ; если воркер пропадёт молча (SIGBUS) — это "
        "оно. Контейнеру нужен --shm-size побольше",
        room // 1024 ** 2, cards, needed // 1024 ** 2)


class StageDriver:
    """Сторона драйвера: менеджер блоков плюс связь с воркерами.

    Это то, что `vllm_batch` знает как «исполнитель»: у него есть
    `kv_cache_manager` и `kv_cache_config`, чтобы собирать батч и отдавать
    блоки. Карты у него нет — `device` не задан нарочно: тензоры с провода
    остаются на процессоре, а на карту их кладёт каждый воркер сам.
    """

    def __init__(self, executor, *, cards: int, is_first: bool, is_last: bool) -> None:
        self.executor = executor
        self.cards = cards
        self.is_first = is_first
        self.is_last = is_last
        self.kv_cache_config = None
        self.kv_cache_manager = None
        #: Запросы, под которые выданы блоки, — чтобы было чем их отпустить.
        self.requests: dict = {}
        # Один шаг за раз: очередь исполнителя не рассчитана на два
        # запроса вперемешку, а батч и так в полёте один.
        self._lock = threading.Lock()

    def layers_built(self) -> int:
        """Сколько слоёв собрал каждый воркер. Разошлись — отказ: карты
        считали бы разные модели."""
        counts = [int(count) for count in
                  self.executor.collective_rpc("stage_layers_built")]
        if len(set(counts)) > 1:
            raise RunnerRefused(
                f"воркеры собрали разное число слоёв: {counts}")
        return counts[0] if counts else 0

    def lay_out_cache(self, config, *, block_size: int, max_model_len: int) -> None:
        self.kv_cache_config, self.kv_cache_manager = lay_out_cache(
            self.executor, config, block_size=block_size,
            max_model_len=max_model_len)

    def run(self, scheduler_output, incoming, *, expected: int):
        """Шаг на всех воркерах; ответ — с нулевого.

        Входящие тензоры уезжают каждому целиком: при tensor parallelism
        вход слоя один на всех картах.
        """
        tensors = None
        if incoming is not None:
            items = incoming.items() if hasattr(incoming, "items") else incoming
            tensors = {name: value.cpu() for name, value in items}
        with self._lock:
            answer = self.executor.collective_rpc(
                "stage_step", args=(scheduler_output, tensors),
                kwargs={"expected": expected}, unique_reply_rank=0,
                timeout=STEP_TIMEOUT_S)
        if answer is None:
            raise RunnerRefused("нулевой воркер не отдал результата шага")
        hidden, logits = answer
        if hidden is None:
            return None, logits
        from vllm.sequence import IntermediateTensors

        return IntermediateTensors(hidden), None

    def close(self) -> None:
        try:
            self.executor.shutdown()
        except Exception:
            logger.debug("исполнитель не разобрался", exc_info=True)


def load_shard(model_path: str, *, start_layer: int, end_layer: int,
               num_model_layers: int, dtype: str = "bfloat16",
               vram_quota_bytes: int = 0, max_model_len: int = 4096,
               block_size: int = 16, max_sequences: int = 64,
               max_batched_tokens: int = 16384) -> LoadedShard:
    """Собрать модель из одних только наших слоёв — на всех картах узла.

    Порядок шагов не переставляется, и каждый стоит там, где стоит:

    1. срез проверяется и веса урезаются до подъёма чего бы то ни было;
    2. конфиг ставится текущим ДО исполнителя. Свежий vLLM спрашивает конфиг
       уже внутри `initialize_model_parallel`, и без него падает на assert'е,
       в котором про конвейер нет ни слова: «Current vLLM config is not
       set... or a CustomOp was instantiated at module import time». Более
       ранние версии конфиг там не трогают, так что поставить его раньше —
       строго безопаснее, чем позже;
    3. исполнитель поднимает воркеры: карта, NCCL-группа, наши подмены и
       загрузка среза — всё это внутри каждого (vllm_worker.py);
    4. кэш раскладывается на все воркеры сразу, когда веса уже на местах и
       видно, сколько осталось.
    """
    require_cuda()
    is_first, is_last = stage_role(start_layer, end_layer, num_model_layers)
    cards = card_count()
    logger.info("собираю слои [%d, %d) из %d: первая=%s, последняя=%s, карт %d",
                start_layer, end_layer, num_model_layers, is_first, is_last,
                cards)

    model_path = prepare_weights(model_path, start_layer=start_layer,
                                 end_layer=end_layer, is_first=is_first,
                                 is_last=is_last, dtype=dtype)

    plan = plan_for_shard(model_path, layers=end_layer - start_layer,
                          dtype=dtype, vram_quota_bytes=vram_quota_bytes,
                          max_sequences=max_sequences,
                          max_model_len=max_model_len, cards=cards)
    logger.info("память: %s; беру %.2f каждой карты", plan.why, plan.utilisation)
    max_sequences = plan.max_sequences

    config = _build_config(model_path, dtype=dtype, max_model_len=max_model_len,
                           utilisation=plan.utilisation, block_size=block_size,
                           max_sequences=max_sequences,
                           max_batched_tokens=max_batched_tokens, cards=cards,
                           stage={"start_layer": start_layer,
                                  "end_layer": end_layer,
                                  "num_model_layers": num_model_layers})
    _hold_config(config)
    warn_if_shm_tight(cards)
    forbid_compile()

    driver = StageDriver(start_executor(config), cards=cards,
                         is_first=is_first, is_last=is_last)
    try:
        built = driver.layers_built()
        wanted = end_layer - start_layer
        if built and built != wanted:
            raise RunnerRefused(
                f"просили {wanted} слоёв, а собралось {built}: приём разошёлся "
                "с этой версией vLLM, и считать она будет не то")
        logger.info("загружено слоёв: %s", built or "не удалось сосчитать")
        driver.lay_out_cache(config, block_size=block_size,
                             max_model_len=max_model_len)
    except BaseException:
        # Воркеры — процессы; брошенные при отказе, они держат карты до
        # тех пор, пока их не убьёт агент.
        driver.close()
        raise
    return LoadedShard(start_layer=start_layer, end_layer=end_layer,
                       num_layers=num_model_layers, is_first=is_first,
                       is_last=is_last, dtype=dtype, runner=driver,
                       max_sequences=max_sequences, cards=cards)


def step(shard: LoadedShard, sequences, *, incoming=None, first_step: bool):
    """Один шаг движка над батчем, который выбрали снаружи.

    Возвращает то же, что и собственный исполнитель стадии: скрытые состояния
    на всех стадиях кроме последней, логиты — на последней. Различать их
    вызывающему не нужно.

    Батч собирается здесь, в драйвере, и уезжает воркерам готовым: блоки
    KV-кэша под него выданы один раз, и все карты получают один и тот же
    состав в одном и том же порядке.
    """
    from looma_stage import vllm_batch

    # Сначала то, что можно проверить, ничего не трогая: пустой батч и
    # отсутствующие тензоры — это не сбой движка, а неправильный вызов, и
    # звучать они должны так же.
    batch = list(sequences)
    if not batch:
        raise RunnerRefused("шаг без единой последовательности")
    if not shard.is_first and incoming is None:
        raise RunnerRefused(
            "неголовной стадии нечего считать: тензоры от предыдущей не пришли")

    runner = shard.runner
    form = vllm_batch.prefill if first_step else vllm_batch.decode
    scheduled = form(batch, runner)
    return runner.run(scheduled, incoming if not shard.is_first else None,
                      expected=len(batch))


def collect(runner, answer, *, is_last: bool, expected: int):
    """Забрать результат шага у исполнителя vLLM — в воркере, сразу после
    `execute_model`. Возвращает `(тензоры, логиты)`, ровно одно из двух."""
    if is_last:
        # Копией и ДО закрытия шага: сэмплер vLLM правит логиты на месте
        # (делит на температуру, режет по top-p), а выбирать токен мы будем
        # сами и по своим правилам — иначе один и тот же промпт даёт разные
        # ответы в зависимости от того, каким движком считали.
        logits = _logits_from(runner, answer, expected=expected).clone()
        _finish_step(runner)
        return None, logits
    return _hidden_from(runner, answer), None


def _finish_step(runner) -> None:
    """Закрыть шаг так, как того требует эта версия vLLM.

    С 0.14 шаг последней стадии разделён надвое: `execute_model` считает
    логиты, кладёт их во временное состояние и возвращает **None**, а забрать
    результат и очистить это состояние обязан `sample_tokens`.

    Не позвать его — значит оставить состояние непустым. Текущий шаг при этом
    проходит целиком: логиты лежат в состоянии, мы их оттуда и берём. Падает
    СЛЕДУЮЩИЙ, на «State error: sample_tokens() must be called after
    execute_model() returns None» — и по этому тексту не видно ни того, что
    виноват предыдущий шаг, ни того, что первый токен уже успел уехать
    клиенту.

    Одиночной проверкой это не ловится вовсе: один шаг всегда проходит.

    Результат `sample_tokens` не нужен — токен выбираем мы. Зовём ради того,
    чтобы движок вернулся в состояние, из которого можно считать дальше.
    """
    finish = getattr(runner, "sample_tokens", None)
    if finish is None or getattr(runner, "execute_model_state", None) is None:
        # Версии до 0.14 отдают всё одним вызовом, закрывать нечего.
        return
    finish(None)


class VllmEngine:
    """Стадия на vLLM, какой её видит `server.py`.

    Всё, что тут есть, — это обёртка вокруг `load_shard` и `step`: сам движок
    выше по файлу и ничего про конвейер Looma не знает. Класс нужен затем, что
    голове нельзя различать движки — она обязана звать одно и то же и получать
    одинаково устроенный ответ.

    Веса грузит он сам, из пути. Отдать ему уже собранную моделью стадию
    нельзя: она заняла бы карту вторым экземпляром тех же слоёв, и на карту,
    которой хватало впритык, стадия просто не поднялась бы.
    """

    #: Батч из нескольких — то, ради чего этот движок вообще нужен.
    batches = True

    def __init__(self, model_path: str, *, start_layer: int, end_layer: int,
                 num_model_layers: int, dtype: str = "bfloat16",
                 vram_quota_bytes: int = 0, max_requests: int = 64,
                 max_model_len: int = 4096, **_options) -> None:
        import torch

        self.torch = torch
        self.shard = load_shard(model_path, start_layer=start_layer,
                                end_layer=end_layer,
                                num_model_layers=num_model_layers, dtype=dtype,
                                vram_quota_bytes=vram_quota_bytes,
                                max_model_len=max_model_len,
                                max_sequences=max_requests)
        self.is_first = self.shard.is_first
        self.is_last = self.shard.is_last
        self.cards = self.shard.cards
        # Сколько мест нашлось под кэш. Голова раздаёт по этому числу, а не по
        # тому, что просили: обещать больше, чем помещается, — это принимать
        # запросы, которые упрутся в нехватку блоков посреди ответа.
        self.capacity = self.shard.max_sequences or max_requests
        self._live: set = set()

    # ------------------------------------------------------------ счёт
    def step_batch(self, sequences, *, incoming=None, first_step: bool):
        """Один шаг над батчем. Возвращает `(карта тензоров, логиты)`.

        Ровно одно из двух не None: на последней стадии логиты, на прочих —
        тензоры. Так же отвечает и собственный исполнитель.
        """
        hidden, logits = step(self.shard, sequences,
                              incoming=self._incoming(incoming),
                              first_step=first_step)
        for sequence in sequences:
            self._live.add(sequence.request_id)
        if hidden is None:
            return None, logits
        return dict(hidden.tensors), None

    def _incoming(self, tensors):
        """Карта тензоров с провода — в то, что понимает vLLM.

        Превращение прячется здесь, а не у зовущего: голова обязана звать оба
        движка одинаково, а `IntermediateTensors` — тип vLLM, и знать о нём
        стадии на собственном исполнителе незачем.
        """
        from vllm.sequence import IntermediateTensors

        if tensors is None or isinstance(tensors, IntermediateTensors):
            return tensors
        # На карту не кладём: карты у драйвера нет, тензоры уедут воркерам с
        # процессора, и каждый положит их на свою.
        return IntermediateTensors(dict(tensors))

    def sample_batch(self, logits, sequences) -> list:
        """По токену на последовательность, в порядке батча."""
        from looma_stage import batch_wire

        rows = logits if getattr(logits, "dim", lambda: 1)() > 1 else logits[None]
        batch_wire.check_rows(list(sequences), int(rows.shape[0]))
        return [self.sample(row, temperature=sequence.temperature,
                            top_p=sequence.top_p, seed=sequence.seed)
                for row, sequence in zip(rows, sequences)]

    def sample(self, logits, *, temperature: float = 0.0, top_p: float = 1.0,
               seed: Optional[int] = None) -> int:
        """Выбор токена — тот же, что у собственного исполнителя.

        Не из vLLM: его сэмплер живёт внутри его же планировщика, которого мы
        как раз обходим. Одинаковый выбор на обоих движках стоит дороже, чем
        экономия на этих десяти строках, — иначе один и тот же промпт даёт
        разные ответы в зависимости от того, чем считали.
        """
        from looma_stage.executor import ShardExecutor

        return ShardExecutor.sample(self, logits, temperature=temperature,
                                    top_p=top_p, seed=seed)

    # ------------------------------------------------------------ уборка
    def free(self, request_id: str) -> None:
        from looma_stage import vllm_batch

        vllm_batch.release(self.shard.runner, request_id)
        self._live.discard(request_id)

    def active_requests(self) -> int:
        return len(self._live)

    def shutdown(self) -> None:
        self.shard.close()
        shutdown()


def _hidden_from(runner, answer):
    """Промежуточные тензоры, как их отдала эта версия vLLM.

    Версии отличаются: одна возвращает их прямо, другая складывает в состояние
    исполнителя. Гадать не будем — если не нашли, скажем, ЧТО получили, чтобы
    первый же прогон на узле это назвал.
    """
    from vllm.sequence import IntermediateTensors

    if isinstance(answer, IntermediateTensors):
        return answer
    for name in ("intermediate_tensors", "hidden_states"):
        found = getattr(answer, name, None) or getattr(
            getattr(runner, "execute_model_state", None), name, None)
        if isinstance(found, IntermediateTensors):
            return found
    raise RunnerRefused(
        f"шаг не отдал промежуточных тензоров, а вернул {type(answer).__name__}; "
        "в этой версии vLLM они лежат где-то ещё")


def _logits_from(runner, answer, *, expected: int):
    """Логиты последней стадии — по строке на последовательность.

    Строка логитов не подписана именем запроса: соответствие держится только
    на порядке батча. Поэтому число строк сверяется с числом последовательностей
    здесь и сразу. Разойдись оно молча — токен уехал бы чужому клиенту, и
    заметить это можно было бы разве что по жалобе на бессвязный ответ.
    """
    state = getattr(runner, "execute_model_state", None)
    for source in (answer, state):
        found = getattr(source, "logits", None)
        if found is None:
            continue
        shape = list(getattr(found, "shape", []) or [])
        rows = shape[0] if len(shape) > 1 else 1
        if rows != expected:
            raise RunnerRefused(
                f"движок вернул {rows} строк логитов на {expected} "
                f"последовательностей (форма {shape}); соответствие строки и "
                "запроса держится только на порядке батча, а он уже разошёлся")
        return found
    raise RunnerRefused(
        f"шаг не отдал логитов, а вернул {type(answer).__name__}; "
        "в этой версии vLLM они лежат где-то ещё")


def _hold_config(config) -> None:
    """Установить текущий конфиг vLLM и не отпускать."""
    global _CONFIG
    import contextlib

    from vllm.config import set_current_vllm_config

    if _CONFIG is not None:
        return
    _CONFIG = contextlib.ExitStack()
    _CONFIG.enter_context(set_current_vllm_config(config))


def shutdown() -> None:
    """Разобрать то, что поднято в ЭТОМ процессе.

    Воркеры разбирает исполнитель (`LoadedShard.close`); здесь — конфиг и
    распределённая группа, если кто-то поднял её в драйвере (одиночная
    проверка старого образца). Без этого torch на выходе жалуется на
    утечку — и жалуется по делу: группа держит дескрипторы и разделяемую
    память.

    Ни один шаг не обязателен: разбираем то, что поднялось, и молчим про
    остальное. Падать на уборке — худшее, что можно сделать с процессом,
    который и так уходит.
    """
    global _CONFIG
    if _CONFIG is not None:
        try:
            _CONFIG.close()
        except Exception:
            logger.debug("контекст конфига не закрылся", exc_info=True)
        _CONFIG = None
    try:
        from vllm.distributed import parallel_state

        parallel_state.destroy_model_parallel()
        parallel_state.destroy_distributed_environment()
    except Exception:
        logger.debug("разбор группы vLLM не прошёл", exc_info=True)
    try:
        import torch

        if torch.distributed.is_initialized():
            torch.distributed.destroy_process_group()
    except Exception:
        logger.debug("разбор группы torch не прошёл", exc_info=True)


def _count_layers(runner) -> int:
    """Сколько слоёв на самом деле собралось.

    Проверка не ради аккуратности: подмена `get_pp_indices` — единственное,
    что удерживает vLLM от сборки всей модели, и её молчаливый провал даёт
    стадию, которая считает всё и ест всю карту.
    """
    model = getattr(runner, "model", None)
    for path in (("model", "layers"), ("layers",)):
        found = model
        for name in path:
            found = getattr(found, name, None)
            if found is None:
                break
        if found is not None:
            try:
                return sum(1 for layer in found if type(layer).__name__ != "PPMissingLayer")
            except TypeError:
                continue
    return 0


def _prompts(shard: LoadedShard, prompts: List[List[int]], *, incoming=None):
    """Prefill над батчем из скольких угодно последовательностей.

    Имена запросов задаются здесь и по порядку — тот же порядок повторит
    следующая стадия, собрав батч из тех же промптов. В этом и смысл проверки:
    состав батча не пересчитывается на каждой стадии, а повторяется, и если бы
    он разъехался, тензоры пришли бы не той длины.
    """
    from looma_stage.scheduler import Sequence

    batch = [Sequence(request_id=f"проверка-{index}", prompt_ids=list(ids))
             for index, ids in enumerate(prompts)]
    return step(shard, batch, incoming=incoming, first_step=True)


def _parse_prompts(text: str) -> List[List[int]]:
    """«1,2,3;4,5» -> [[1,2,3],[4,5]]. Пустые промпты отбрасываются: батч из
    пустой последовательности vLLM примет и посчитает ни за чем."""
    prompts = []
    for chunk in text.split(";"):
        ids = [int(piece) for piece in chunk.split(",") if piece.strip()]
        if ids:
            prompts.append(ids)
    if not prompts:
        raise RunnerRefused(f"в --prompt-ids не нашлось ни одного токена: {text!r}")
    return prompts


def _save_hidden(tensors, path: str) -> dict:
    """Сложить промежуточные тензоры в файл нашим же форматом провода.

    Через `wire`, а не pickle: это тот самый формат, которым они поедут между
    машинами, и проверить его заодно — бесплатно.
    """
    import json

    import torch

    from looma_stage import wire

    saved = {}
    blob = bytearray()
    for name, tensor in tensors.items():
        data, shape, dtype = wire.to_wire(torch, tensor)
        saved[name] = {"at": len(blob), "size": len(data), "shape": shape,
                       "dtype": dtype}
        blob.extend(data)
    with open(path, "wb") as handle:
        handle.write(blob)
    with open(path + ".json", "w") as handle:
        json.dump(saved, handle)
    return saved


def _load_hidden(path: str, device=None):
    """Обратно, в тензоры — на процессоре, если карта не названа: драйвер
    её не держит, воркеры положат сами."""
    import json

    import torch

    from looma_stage import wire
    from vllm.sequence import IntermediateTensors

    with open(path + ".json") as handle:
        layout = json.load(handle)
    blob = pathlib_read(path)
    restored = {}
    for name, where in layout.items():
        piece = blob[where["at"]:where["at"] + where["size"]]
        restored[name] = wire.from_wire(torch, piece, where["shape"],
                                        where["dtype"])
        if device is not None:
            restored[name] = restored[name].to(device)
    return IntermediateTensors(restored)


def pathlib_read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def main(argv: Optional[List[str]] = None) -> int:
    """Проверка на узле.

    Без аргументов про тензоры — только загрузка (веха 1). С `--dump-hidden`
    прогоняет промпт и складывает промежуточные тензоры в файл; с
    `--load-hidden` поднимает следующую стадию, читает их и печатает логиты.

    Двумя прогонами, а не одним процессом: группа конвейера у vLLM глобальная,
    и две стадии рядом дрались бы за неё. Заодно проверяется формат провода —
    тот самый, которым тензоры поедут между машинами.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="looma_stage.vllm_engine",
        description="Загрузить срез слоёв через vLLM и, по желанию, прогнать шаг")
    parser.add_argument("--weights", required=True, help="путь или репозиторий")
    parser.add_argument("--start-layer", type=int, required=True)
    parser.add_argument("--end-layer", type=int, required=True)
    parser.add_argument("--num-model-layers", type=int, required=True)
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--vram-quota-bytes", type=int, default=0)
    parser.add_argument("--prompt-ids", default="",
                        help="токены через запятую; несколько промптов — через "
                             "точку с запятой, и тогда шаг считает их батчем")
    parser.add_argument("--dump-hidden", default="",
                        help="куда сложить промежуточные тензоры")
    parser.add_argument("--load-hidden", default="",
                        help="откуда их взять — для стадии, которая не первая")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    answer: dict = {}
    shard = None
    try:
        shard = load_shard(args.weights, start_layer=args.start_layer,
                           end_layer=args.end_layer,
                           num_model_layers=args.num_model_layers,
                           dtype=args.dtype,
                           vram_quota_bytes=args.vram_quota_bytes)
        answer.update(shard.as_dict())

        if args.prompt_ids:
            prompts = _parse_prompts(args.prompt_ids)
            incoming = (_load_hidden(args.load_hidden, None)
                        if args.load_hidden else None)
            answer["последовательностей в батче"] = len(prompts)
            answer["токенов в батче"] = sum(len(ids) for ids in prompts)
            hidden, logits = _prompts(shard, prompts, incoming=incoming)
            if hidden is not None:
                answer["отдала"] = "скрытые состояния"
                answer["тензоры"] = sorted(hidden.tensors)
                if args.dump_hidden:
                    answer["сложено"] = _save_hidden(hidden.tensors, args.dump_hidden)
            if logits is not None:
                answer["отдала"] = "логиты"
                answer["форма логитов"] = list(getattr(logits, "shape", []))
                # По строке на последовательность, в порядке батча. Печатаем
                # выбранные токены: одинаковые токены на разных промптах —
                # первый признак, что батч склеился в одну последовательность.
                answer["токены"] = [int(row.argmax().item()) for row in
                                    (logits if logits.dim() > 1 else logits[None])]
    except RunnerRefused as exc:
        print(f"не вышло: {exc}")
        return 2
    finally:
        if shard is not None:
            shard.close()
        shutdown()
    print(json.dumps(answer, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
