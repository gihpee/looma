"""Тренер: расписание шагов, эпохи, метрики, сборка адаптера.

Расписание — GPipe: все микробатчи батча вперёд, все назад, один шаг.
Просто и детерминированно; истинная конвейерная загрузка (1F1B, несколько
микробатчей в полёте на разных стадиях одновременно) — следующая ступень,
и для неё транспорт должен уметь отвечать позже. Здесь всё синхронно.

Голова — единственное место, где есть датасет и цикл. Стадии ничего про
эпохи не знают: им приходят микробатчи, градиенты и команда «шаг».
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from looma_stage.train import data as data_mod
from looma_stage.train import lora as lora_mod
from looma_stage.train.transport import Transport, unpack

logger = logging.getLogger("looma_stage.train.head")


@dataclass(frozen=True)
class Schedule:
    epochs: int = 1
    batch_size: int = 8
    micro_size: int = 2
    lr: float = 2e-4
    warmup_steps: int = 0
    #: Обрезка общей нормы градиента адаптеров; 0 — без обрезки.
    max_grad_norm: float = 1.0
    #: Каждые сколько шагов сохранять чекпоинт; 0 — не сохранять.
    save_every: int = 0
    shuffle_seed: Optional[int] = 0

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "Schedule":
        raw = raw or {}
        return cls(epochs=int(raw.get("epochs") or 1),
                   batch_size=int(raw.get("batch_size") or 8),
                   micro_size=int(raw.get("micro_size") or 2),
                   lr=float(raw.get("lr") or 2e-4),
                   warmup_steps=int(raw.get("warmup_steps") or 0),
                   max_grad_norm=float(raw.get("max_grad_norm", 1.0)),
                   save_every=int(raw.get("save_every") or 0),
                   shuffle_seed=raw.get("shuffle_seed", 0))


@dataclass
class StepReport:
    step: int
    epoch: int
    loss: float
    tokens: int
    lr: float
    grad_norm: float
    seconds: float

    def as_dict(self) -> dict:
        return {"step": self.step, "epoch": self.epoch, "loss": self.loss,
                "tokens": self.tokens, "lr": self.lr, "grad_norm": self.grad_norm,
                "seconds": self.seconds,
                "tokens_per_s": self.tokens / self.seconds if self.seconds else 0.0}


class Trainer:
    """Гонит батчи через транспорт и делает шаги."""

    def __init__(self, transport: Transport, *, schedule: Schedule, pad_id: int,
                 on_report: Optional[Callable[[StepReport], None]] = None,
                 checkpoint_dir: Optional[str] = None) -> None:
        self.transport = transport
        self.schedule = schedule
        self.pad_id = pad_id
        self.on_report = on_report or (lambda report: None)
        self.checkpoint_dir = checkpoint_dir
        self.step_no = 0
        self.total_steps = 0

    # ------------------------------------------------------------ обучение
    def fit(self, examples: Sequence[data_mod.Example]) -> List[StepReport]:
        schedule = self.schedule
        per_epoch = math.ceil(len(examples) / schedule.batch_size)
        self.total_steps = per_epoch * schedule.epochs
        reports: List[StepReport] = []
        logger.info("обучение: %d примеров, %d эпох, %d шагов, %d стадий",
                    len(examples), schedule.epochs, self.total_steps, self.transport.size)
        for epoch in range(schedule.epochs):
            seed = None if schedule.shuffle_seed is None else schedule.shuffle_seed + epoch
            for micros in data_mod.batches(examples, batch_size=schedule.batch_size,
                                           micro_size=schedule.micro_size,
                                           shuffle_seed=seed):
                report = self.train_batch(micros, epoch=epoch)
                reports.append(report)
                self.on_report(report)
                if (schedule.save_every and self.checkpoint_dir
                        and self.step_no % schedule.save_every == 0):
                    self.transport.save(f"{self.checkpoint_dir}/step-{self.step_no}")
        return reports

    def train_batch(self, micros: Sequence[Sequence[data_mod.Example]], *, epoch: int = 0) -> StepReport:
        """Один батч: все микробатчи вперёд и назад, потом шаг."""
        started = time.perf_counter()
        batch_id = uuid.uuid4().hex[:12]
        collated = [data_mod.collate(micro, pad_id=self.pad_id) for micro in micros]
        # Доля каждого микробатча в батче — чтобы градиент сложился в
        # среднее по всем токенам батча, а не в сумму средних.
        counts = [sum(1 for row in c["labels"] for label in row[1:] if label != data_mod.IGNORE)
                  for c in collated]
        total = sum(counts) or 1

        loss_sum = 0.0
        for index, (collated_micro, count) in enumerate(zip(collated, counts)):
            loss_sum += self._run_micro(batch_id, index, collated_micro, scale=count / total)

        lr = self._lr()
        grad_norm = self._clip_and_step(lr)
        seconds = time.perf_counter() - started
        return StepReport(step=self.step_no, epoch=epoch, loss=loss_sum, tokens=total,
                          lr=lr, grad_norm=grad_norm, seconds=seconds)

    def _run_micro(self, batch_id: str, micro: int, collated: dict, *, scale: float) -> float:
        """Микробатч через все стадии вперёд и назад. Возвращает его вклад в loss."""
        import torch

        transport = self.transport
        last = transport.size - 1
        common = {"batch_id": batch_id, "micro": micro,
                  "attention_mask": collated["attention_mask"],
                  "position_ids": collated["position_ids"]}
        message = {**common, "input_ids": collated["input_ids"]}
        if last == 0:
            message.update(labels=collated["labels"], loss_scale=scale)
        answer = transport.forward(0, message)

        for stage in range(1, last + 1):
            tensors = unpack(torch, answer.get("tensors"))
            message = {**common, "tensors": _repack(torch, {"hidden": tensors["hidden"]})}
            if stage == last:
                message.update(labels=collated["labels"], loss_scale=scale)
            answer = transport.forward(stage, message)

        loss = float(answer.get("loss") or 0.0)
        # Назад: последняя уже сделала свой backward и отдала градиент по
        # входу; каждая предыдущая досчитывает до своего входа.
        for stage in range(last - 1, -1, -1):
            grad = unpack(torch, answer.get("tensors")).get("grad")
            if grad is None:
                raise RuntimeError(f"стадия {stage + 1} не отдала градиента по входу")
            answer = transport.backward(stage, {"batch_id": batch_id, "micro": micro,
                                                "tensors": _repack(torch, {"grad": grad})})
        return loss

    def _clip_and_step(self, lr: float) -> float:
        """Общая норма градиента по всем стадиям — и один коэффициент
        обрезки на всех. Возвращает норму до обрезки."""
        norms = self.transport.norms()
        total = math.sqrt(sum(norms))
        scale = 1.0
        limit = self.schedule.max_grad_norm
        if limit and total > limit:
            scale = limit / (total + 1e-6)
        self.transport.step(lr=lr, grad_scale=scale)
        self.step_no += 1
        return total

    def _lr(self) -> float:
        """Линейный разогрев, потом линейное затухание до нуля — самое
        обычное расписание для дообучения."""
        schedule = self.schedule
        step = self.step_no
        if schedule.warmup_steps and step < schedule.warmup_steps:
            return schedule.lr * (step + 1) / schedule.warmup_steps
        if self.total_steps <= schedule.warmup_steps:
            return schedule.lr
        remaining = max(0, self.total_steps - step)
        span = max(1, self.total_steps - schedule.warmup_steps)
        return schedule.lr * remaining / span

    # ------------------------------------------------------------ результат
    def collect_adapter(self, *, settings: lora_mod.LoraSettings, base_model: str,
                        directory: str):
        """Собрать куски адаптера со всех стадий в один PEFT-адаптер."""
        pieces = self.transport.collect()
        merged = lora_mod.merge_pieces(pieces)
        return lora_mod.write_adapter(directory, merged,
                                      lora_mod.adapter_config(settings, base_model=base_model))


def _repack(torch, tensors: Dict[str, object]) -> dict:
    from looma_stage.train.transport import pack

    return pack(torch, tensors)
