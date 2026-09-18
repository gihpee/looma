"""Что за модель просят и как её разрезать между узлами.

Здесь ровно два вопроса: сколько в модели слоёв, и кому какие достанутся.
Всё остальное про модель знает сама стадия — оркестратор её не загружает и
весов не видит.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from looma.logging_config import get_logger

logger = get_logger(__name__)

HF_CONFIG = "https://huggingface.co/{repo}/resolve/main/config.json"


class ModelError(ValueError):
    """Про эту модель нельзя ответить на нужные вопросы, и вот почему."""


@dataclass(frozen=True)
class ModelInfo:
    repo: str
    num_layers: int
    hidden_size: int = 0
    architecture: str = ""
    # Остальное — чтобы посчитать, сколько весит слой, не скачивая весов.
    # Нули означают «в конфиге не сказано»; оценка тогда берёт запас.
    intermediate_size: int = 0
    num_attention_heads: int = 0
    num_key_value_heads: int = 0
    head_dim: int = 0
    vocab_size: int = 0
    tie_word_embeddings: bool = False
    quantization: str = ""

    def as_dict(self) -> dict:
        return {
            "repo": self.repo,
            "num_layers": self.num_layers,
            "hidden_size": self.hidden_size,
            "architecture": self.architecture,
            "intermediate_size": self.intermediate_size,
            "num_attention_heads": self.num_attention_heads,
            "num_key_value_heads": self.num_key_value_heads,
            "vocab_size": self.vocab_size,
            "quantization": self.quantization,
            "layer_params": self.layer_params,
            "params": self.params,
        }

    @property
    def layer_params(self) -> int:
        """Параметров в одном слое декодера: внимание + MLP. Нормы не в
        счёт — их доли процента."""
        h = self.hidden_size
        if not h:
            return 0
        heads = self.num_attention_heads or 1
        kv = self.num_key_value_heads or heads
        head_dim = self.head_dim or h // heads
        attention = h * heads * head_dim * 2 + h * kv * head_dim * 2
        mlp = 3 * h * (self.intermediate_size or 4 * h)
        return attention + mlp

    @property
    def params(self) -> int:
        """Всего, вместе с эмбеддингами и головой."""
        if not self.hidden_size:
            return 0
        ends = self.vocab_size * self.hidden_size * (1 if self.tie_word_embeddings else 2)
        return self.layer_params * self.num_layers + ends


def describe(repo: str, *, token: str = "") -> ModelInfo:
    """Прочитать config.json модели на HuggingFace.

    Одно короткое обращение к сети и никакой загрузки весов: чтобы разложить
    модель по узлам, достаточно знать число слоёв. Веса качает та стадия,
    которой они нужны, и только свой кусок.
    """
    repo = (repo or "").strip().strip("/")
    if not repo or "/" not in repo:
        raise ModelError(
            f"{repo!r} не похоже на имя модели: нужно 'владелец/название', "
            "например 'Qwen/Qwen3-8B'"
        )
    # Проверяем состав имени до того, как оно попадёт в URL: иначе кириллица
    # или пробел вылезают ошибкой кодировки из глубины urllib, где про модель
    # уже ничего не сказано.
    if not all(c.isascii() and (c.isalnum() or c in "-_./") for c in repo):
        raise ModelError(
            f"{repo!r} не может быть именем на HuggingFace: там латиница, цифры "
            "и -_./"
        )
    request = urllib.request.Request(HF_CONFIG.format(repo=repo))
    token = token or os.environ.get("HF_TOKEN", "").strip()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            config = json.loads(answer.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ModelError(
                f"{repo} закрыта: нужен HF_TOKEN с доступом к ней"
            ) from None
        if exc.code == 404:
            raise ModelError(f"на HuggingFace нет {repo}") from None
        raise ModelError(f"HuggingFace ответил {exc.code} про {repo}") from None
    except (urllib.error.URLError, ValueError) as exc:
        raise ModelError(f"не удалось прочитать config.json у {repo}: {exc}") from None

    layers = config.get("num_hidden_layers") or config.get("n_layer")
    if not layers:
        raise ModelError(
            f"в config.json у {repo} не сказано число слоёв — такую модель "
            "нельзя разрезать между узлами"
        )
    architectures = config.get("architectures") or []
    quantization = config.get("quantization_config") or {}
    return ModelInfo(
        repo=repo,
        num_layers=int(layers),
        hidden_size=int(config.get("hidden_size") or 0),
        architecture=architectures[0] if architectures else "",
        intermediate_size=int(config.get("intermediate_size") or 0),
        num_attention_heads=int(config.get("num_attention_heads") or 0),
        num_key_value_heads=int(config.get("num_key_value_heads") or 0),
        head_dim=int(config.get("head_dim") or 0),
        vocab_size=int(config.get("vocab_size") or 0),
        tie_word_embeddings=bool(config.get("tie_word_embeddings", False)),
        quantization=str(quantization.get("quant_method") or "") if quantization else "",
    )


def expand_ranks(nodes: list, engine: str) -> list:
    """Из выбранных узлов — ранги: кто где живёт и сколько у него VRAM.

    Ранг — это узел, для обоих движков. Разница в том, сколько памяти узла
    ранг может занять:

    torch — всю: его загрузчик кладёт срез на все карты машины пропорционально
    их памяти, стык между картами идёт по PCIe внутри процесса
    (looma_stage/loader.py, plan_layer_devices).

    vLLM — `N × min(карта)`: стадия поднимается на всех картах узла с tensor
    parallelism (looma_stage/vllm_worker.py), а TP режет каждый слой поровну,
    так что меньшая карта задаёт предел всем. Сумма карт была бы обещанием
    памяти, которую большие карты не могут отдать за маленькую: на 24+12 ГБ
    стадия возьмёт 2×12, а не 36.

    Разворачивать многокарточную машину в несколько рангов через локальную
    доставку НЕ делается намеренно: карты одной машины должны говорить по
    NCCL, а не через сокет агента.

    Агент постарше не шлёт память по картам — тогда сумма делится поровну и
    берётся вся: поровну поделённые карты и есть `N × min`.
    """
    ranks = []
    for node in nodes:
        total = int(node.get("vram_free_bytes") or 0)
        if engine != "vllm":
            ranks.append({"node_id": node["node_id"], "vram": total})
            continue
        per_gpu = [int(v) for v in (node.get("vram_free_per_gpu") or []) if int(v) > 0]
        if not per_gpu:
            count = max(1, int(node.get("gpus_total") or 1))
            per_gpu = [total // count] * count
        ranks.append({"node_id": node["node_id"],
                      "vram": len(per_gpu) * min(per_gpu)})
    return ranks


# ------------------------------------------------------------ обучение
#: Байт на параметр базы по точности. NF4 — полбайта плюс масштабы.
PRECISION_BYTES = {"bf16": 2.0, "fp16": 2.0, "fp32": 4.0, "nf4": 0.55}


def training_stage_bytes(model: "ModelInfo", *, layers: int, is_first: bool,
                         is_last: bool, precision: str, lora_r: int,
                         micro_tokens: int, micros_in_flight: int,
                         dtype_bytes: float = 2.0) -> dict:
    """Сколько памяти попросит стадия обучения — по слагаемым.

    Оценка, а не измерение, и в каждом слагаемом заложен запас: недобрать
    хуже, чем перебрать — нехватка вылезет посреди эпохи, а не при подъёме.

    - веса среза в выбранной точности (эмбеддинги и голова — bf16 всегда);
    - адаптеры в float32 и оптимизатор AdamW (ещё два таких же);
    - активации: при пересчёте по слоям на forward хранится вход каждого
      слоя каждого микробатча в полёте, плюс рабочее место одного слоя
      целиком на backward (внимание × длина);
    - логиты на последней стадии: токены × словарь × 4 байта — у моделей
      со словарём 150k это гигабайты;
    - запас 1.5 ГБ на CUDA-контекст и фрагментацию.
    """
    bytes_per_param = PRECISION_BYTES.get((precision or "bf16").lower(), 2.0)
    h = model.hidden_size or 4096
    weights = model.layer_params * layers * bytes_per_param
    ends = model.vocab_size * h * dtype_bytes
    if is_first:
        weights += ends
    if is_last and not (is_first and model.tie_word_embeddings):
        weights += ends
    # LoRA на семи проекциях: r × (in + out) на каждую; грубо 7 × r × 2 × h
    # плюс MLP-проекции пошире. Считаем как r × 2 × (сумма in+out).
    inter = model.intermediate_size or 4 * h
    per_layer_adapter = lora_r * (4 * 2 * h + 3 * (h + inter))
    adapters = per_layer_adapter * layers * 4.0
    optimizer = adapters * 2
    per_micro = micro_tokens * h * dtype_bytes
    activations = per_micro * layers * micros_in_flight + per_micro * 12
    logits = micro_tokens * model.vocab_size * 4.0 if is_last else 0
    overhead = 1.5 * 1024 ** 3
    total = weights + adapters + optimizer + activations + logits + overhead
    return {"weights": int(weights), "adapters": int(adapters), "optimizer": int(optimizer),
            "activations": int(activations), "logits": int(logits),
            "overhead": int(overhead), "total": int(total)}


def training_refusal(model: "ModelInfo", engine_precision: str) -> str:
    """Почему эту модель нельзя дообучать нашим движком. Пусто — можно."""
    if model.quantization:
        return (f"чекпоинт {model.repo} квантован ({model.quantization}); переносимый "
                "движок считает только по bf16/fp16-весам — возьмите неквантованную "
                "версию модели")
    if not model.hidden_size:
        return f"в config.json у {model.repo} нет hidden_size — не оценить память"
    return ""


def split_layers(num_layers: int, stages: int,
                 weights: Optional[List[float]] = None) -> List[Tuple[int, int]]:
    """Кому какой диапазон слоёв.

    По умолчанию поровну. Если переданы веса (свободная VRAM узлов) — слои
    делятся пропорционально им: на стенде из 4090 и 3090 равный разрез
    упирается в меньшую карту, и половина большей простаивает.

    Каждой стадии достаётся хотя бы один слой: стадия без слоёв — это лишний
    сетевой переход, который ничего не считает.
    """
    if stages < 1:
        raise ModelError("нужна хотя бы одна стадия")
    if stages > num_layers:
        raise ModelError(
            f"в модели {num_layers} слоёв, а стадий просят {stages}: "
            "стадия без слоёв — это сетевой переход, который ничего не считает"
        )
    # Сначала каждому по одному слою — стадия без слоёв это сетевой переход,
    # который ничего не считает, — а остаток раздаём по долям. Так сумма сходится
    # по построению, без округления и починки округления после него.
    share = [1] * stages
    rest = num_layers - stages
    if weights and len(weights) == stages and sum(weights) > 0:
        portions = [w / sum(weights) for w in weights]
    else:
        portions = [1 / stages] * stages
    given = [int(rest * portion) for portion in portions]
    # Целые части розданы; дробные хвосты решают, кому достанутся оставшиеся
    # слои — начиная с того, у кого хвост длиннее.
    leftover = rest - sum(given)
    order = sorted(range(stages), key=lambda i: -(rest * portions[i] - given[i]))
    for i in order[:leftover]:
        given[i] += 1
    share = [one + extra for one, extra in zip(share, given)]

    ranges: List[Tuple[int, int]] = []
    start = 0
    for size in share:
        ranges.append((start, start + size))
        start += size
    return ranges


def stage_payload() -> Dict[str, bytes]:
    """Файлы стадии, которые уедут в задачу как её вход.

    Механизм общий для всех нагрузок — см. orchestrator/payloads.py; здесь
    только имя и то, во что превращается его отсутствие для вызывающего.
    """
    from looma.orchestrator.payloads import PayloadMissing, collect

    try:
        return collect("looma_stage", human="кода стадии", dirs=_payload_dirs())
    except PayloadMissing as exc:
        raise ModelError(str(exc)) from None


def _payload_dirs():
    """Где может лежать код стадии, в порядке доверия."""
    from looma.orchestrator.payloads import payload_dirs

    return payload_dirs("looma_stage")


# --------------------------------------------------------------- движки
# Какой драйвер нужен vLLM, и почему именно этот.
#
# vLLM жёстко требует конкретную версию torch (0.28 — ровно 2.13.0). Сборки
# torch лежат на индексах по версиям CUDA, и версии там разные: на `cu124`
# последняя — 2.6.0, нужной нет вовсе. Узел с таким драйвером поставит vLLM
# «успешно»: pip увидит несовпадение и доставит torch с обычного PyPI, поверх
# правильной сборки. Каталог окружения останется с именем cu124, а внутри
# окажется torch под драйвер новее.
#
# Падает это не на установке, а через десять минут — при первом обращении к
# карте, сообщением «CUDA initialization: driver is too old», в котором ни
# слова про подмену. Поэтому решение принимается здесь, до запуска.
# Ниже 12.6 нет сборки torch, которую требует закреплённый vLLM (2.9.1 лежит
# на cu126 и новее; на cu124 последняя вообще 2.6.0). Узел с таким драйвером
# поставит vLLM «успешно», подменив torch колесом с PyPI, и упадёт только при
# первом обращении к карте.
MIN_CUDA_FOR_VLLM = (12, 6)

# Версия vLLM — часть нашего контракта, а не плавающая зависимость.
#
# Стадия лезет в его внутренности: подменяет `get_pp_indices`, снимает проверки
# инициализации весов, собирает `SchedulerOutput` в обход его планировщика,
# спрашивает спецификацию KV-кэша у исполнителя. Всё это приватные API, и они
# меняются между версиями молча.
#
# Без версии в строке требований это не просто «поставится свежий». Имя
# окружения на узле считается от СТРОК требований (agent/looma_agent/tasks/
# spec.py), поэтому `vllm` без пина даёт один и тот же отпечаток для любой
# версии: узел, собравший окружение месяц назад, держит vLLM той поры и не
# обновится никогда, а соседний соберёт сегодняшний. Две стадии одного
# конвейера — на разных версиях, под одним именем каталога.
#
# Меняя пин, мы меняем строку, отпечаток и, значит, окружение: узлы соберут
# новое вместо того, чтобы тихо остаться на старом. Это и есть механизм
# обновления, другого тут нет.
#
# 0.14.0, а не свежая: это единственная версия, на которой стадия проверена на
# живой карте от загрузки среза до логитов. Свежая уже показала одно
# расхождение во внутренностях (конфиг требуется раньше распределённой группы),
# и переход на неё — отдельная работа, а не побочный эффект установки.
VLLM_PIN = "vllm==0.14.0"

# Торч под vLLM пинится РОВНО той версией, которую он требует, и это не
# аккуратность.
#
# Агент ставит torch первым проходом из индекса по драйверу узла, а всё
# остальное — вторым, из обычного PyPI. Если версия там не совпадёт с той, что
# требует vLLM, второй проход молча доставит torch с PyPI поверх правильной
# сборки: каталог окружения останется с именем cu128, а внутри окажется чужое
# колесо. Падает это не на установке, а при первом обращении к карте.
#
# Совпадающий пин лишает второй проход повода что-либо менять. Версии здесь
# берутся из метаданных самого vLLM (`requires_dist`), а не подбираются.
VLLM_TORCH = ("torch==2.9.1", "torchvision==0.24.1", "torchaudio==2.9.1")

# Остальное намеренно без версий: мы зовём только их публичные API. `torch` без
# пина оставлен переносимому движку — там сборку выбирает агент по драйверу
# узла, и наборы версий на индексах cu124/cu126/cu128 разные, так что пин,
# годный для одного узла, сделал бы неустановимым другой.
STAGE_REQUIREMENTS = ("torch", "transformers", "safetensors", "huggingface-hub")

# C-компилятор для Triton — pip-пакетом, а не в образе агента.
#
# Triton при первом запуске любого своего ядра компилирует маленький C-модуль
# (`cuda_utils`) и без компилятора падает: «Failed to find C compiler». В
# образе агента компилятора нет намеренно — образ тонкий, и владелец узла
# обновляет его руками. У плотных моделей (Qwen3) Triton на горячем пути не
# нужен, у MoE (gpt-oss, Mixtral, DeepSeek) и у моделей с attention sinks —
# единственная реализация в vLLM, обойти нельзя.
#
# `ziglang` — колесо с `zig cc` внутри (clang-совместимый компилятор, ~90 МБ),
# ставится как обычный пакет в окружение задачи. Стадия сама пишет обёртку и
# ставит CC на неё (looma_stage/vllm_engine.py: provide_compiler). Проверено
# на стенде (nv3): cuda_utils Triton собирается, одно безобидное
# предупреждение про _POSIX_C_SOURCE.
#
# С версией — по той же причине, что и vLLM: отпечаток окружения считается
# от строк, и непинованный пакет означал бы разные компиляторы под одним
# именем каталога.
TRITON_COMPILER = "ziglang==0.16.0"


def stage_requirements(engine: str) -> List[str]:
    """Что поставить на узле под этот движок."""
    if engine != "vllm":
        return list(STAGE_REQUIREMENTS)
    # Свой torch вместо непинованного: иначе первый проход поставит с индекса
    # одну версию, а vLLM вторым проходом стянет другую с PyPI.
    rest = [name for name in STAGE_REQUIREMENTS if name != "torch"]
    return [*VLLM_TORCH, *rest, VLLM_PIN, TRITON_COMPILER]


def cuda_tuple(version: str) -> Optional[Tuple[int, int]]:
    """«12.4» -> (12, 4). Ничего не разобрали — None, а не догадка."""
    parts = (version or "").strip().split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None


def vllm_refusal(node: dict) -> str:
    """Почему на этом узле нельзя vLLM. Пусто — значит можно.

    Отказ, а не предупреждение: «можно, но сломается» — это то же самое, что
    нельзя, только узнаётся дороже.
    """
    need = ".".join(str(part) for part in MIN_CUDA_FOR_VLLM)
    reported = (node.get("cuda_version") or "").strip()
    found = cuda_tuple(reported)
    if found is None:
        return (f"{node.get('node_id', 'узел')} не сообщил версию CUDA — "
                f"похоже, карты на нём нет; vLLM нужна CUDA {need} или новее")
    if found < MIN_CUDA_FOR_VLLM:
        return (f"на {node.get('node_id', 'узле')} драйвер под CUDA {reported}, "
                f"а vLLM нужна {need} или новее: под {reported} нет сборки "
                "torch, которую он требует. Обновите драйвер узла или берите "
                "движок transformers")
    return ""
