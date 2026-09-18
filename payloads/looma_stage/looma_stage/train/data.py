"""Диалоги → токены и метки; микробатчи.

Формат входа один: JSONL, в каждой строке `{"messages": [{"role", "content"},
…]}` — как у OpenAI API. Шаблон чата берётся у токенизатора модели, своего
реестра нет: у всех современных моделей он лежит рядом с весами, и это
единственный способ учить модель ровно в том формате, в котором она потом
будет отвечать.

Метки: loss считается только по ответу ассистента (SFT). Промпт — всё до
последнего сообщения ассистента включая его заголовок — помечается `-100`.
Граница находится тем же шаблоном: токены промпта с `add_generation_prompt`
обязаны быть префиксом токенов полного диалога; если нет (шаблон меняет
хвост при добавлении ответа) — берётся самый длинный общий префикс, и
считается это вслух.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Sequence

logger = logging.getLogger("looma_stage.train.data")

IGNORE = -100


class DataRefused(RuntimeError):
    """Данные не годятся, и вот почему."""


@dataclass
class Example:
    input_ids: List[int]
    labels: List[int]

    def __len__(self) -> int:
        return len(self.input_ids)

    @property
    def trainable(self) -> int:
        return sum(1 for label in self.labels if label != IGNORE)


def _ids(encoded) -> List[int]:
    if hasattr(encoded, "keys") and "input_ids" in encoded:
        encoded = encoded["input_ids"]
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    encoded = list(encoded)
    if encoded and isinstance(encoded[0], (list, tuple)):
        encoded = list(encoded[0])
    return [int(t) for t in encoded]


def encode_dialog(tokenizer, messages: Sequence[dict], *, max_len: int) -> Example:
    """Один диалог → токены и метки. Последнее сообщение обязано быть от
    ассистента — иначе учить нечему."""
    if not messages or messages[-1].get("role") != "assistant":
        raise DataRefused("последнее сообщение диалога должно быть от assistant")
    if not getattr(tokenizer, "chat_template", None):
        raise DataRefused(
            "у токенизатора нет шаблона чата; без него неизвестно, как модель "
            "видит диалог. Проверьте, что chat_template.jinja доехал вместе с моделью")
    full = _ids(tokenizer.apply_chat_template(list(messages), tokenize=True,
                                              add_generation_prompt=False))
    prompt = _ids(tokenizer.apply_chat_template(list(messages[:-1]), tokenize=True,
                                                add_generation_prompt=True))
    boundary = _common_prefix(prompt, full)
    if boundary != len(prompt):
        logger.warning("шаблон меняет промпт при добавлении ответа: граница "
                       "loss взята по общему префиксу (%d из %d токенов промпта)",
                       boundary, len(prompt))
    labels = [IGNORE] * boundary + full[boundary:]
    if max_len and len(full) > max_len:
        full, labels = full[:max_len], labels[:max_len]
    return Example(input_ids=full, labels=labels)


def _common_prefix(a: List[int], b: List[int]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def read_jsonl(path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise DataRefused(f"{path}:{line_no}: не JSON ({exc})") from None


def examples_from_jsonl(path, tokenizer, *, max_len: int) -> List[Example]:
    """Весь файл в память — датасеты для LoRA измеряются мегабайтами, и
    держать их в памяти дешевле, чем перечитывать на каждой эпохе."""
    made: List[Example] = []
    dropped = 0
    for line_no, row in enumerate(read_jsonl(path), 1):
        if row.get("input_ids"):
            # Уже токенизировано — клиентом или тестом. Метки по умолчанию —
            # все токены (нет промпта, учим всё).
            ids = [int(t) for t in row["input_ids"]]
            labels = [int(t) for t in row.get("labels") or ids]
            if len(labels) != len(ids):
                raise DataRefused(f"{path}:{line_no}: labels и input_ids разной длины")
            example = Example(input_ids=ids[:max_len] if max_len else ids,
                              labels=labels[:max_len] if max_len else labels)
        else:
            messages = row.get("messages")
            if not messages:
                raise DataRefused(f"{path}:{line_no}: нет ни messages, ни input_ids")
            example = encode_dialog(tokenizer, messages, max_len=max_len)
        if example.trainable == 0:
            dropped += 1        # ответ целиком обрезался длиной — учить нечему
            continue
        made.append(example)
    if dropped:
        logger.warning("%d примеров отброшено: ответ не поместился в %d токенов",
                       dropped, max_len)
    if not made:
        raise DataRefused(f"{path}: ни одного пригодного примера")
    logger.info("датасет: %d примеров, %d токенов под loss", len(made),
                sum(e.trainable for e in made))
    return made


def collate(examples: Sequence[Example], *, pad_id: int) -> dict:
    """Примеры разной длины → один батч с паддингом справа.

    Позиции у паддинга продолжаются (не важно, он не под loss и не в маске
    ключей), метки — `-100`.
    """
    length = max(len(e) for e in examples)
    input_ids, labels, mask, positions = [], [], [], []
    for example in examples:
        pad = length - len(example)
        input_ids.append(list(example.input_ids) + [pad_id] * pad)
        labels.append(list(example.labels) + [IGNORE] * pad)
        mask.append([1] * len(example) + [0] * pad)
        positions.append(list(range(length)))
    return {"input_ids": input_ids, "labels": labels, "attention_mask": mask,
            "position_ids": positions}


def batches(examples: Sequence[Example], *, batch_size: int, micro_size: int,
            shuffle_seed: Optional[int] = None) -> Iterator[List[dict]]:
    """Батчи, каждый — список микробатчей (уже склеенных `collate` нужно
    делать вызывающему: ему известен pad_id)."""
    order = list(range(len(examples)))
    if shuffle_seed is not None:
        import random

        random.Random(shuffle_seed).shuffle(order)
    for start in range(0, len(order), batch_size):
        chunk = [examples[i] for i in order[start:start + batch_size]]
        micros = [chunk[i:i + micro_size] for i in range(0, len(chunk), micro_size)]
        yield micros
