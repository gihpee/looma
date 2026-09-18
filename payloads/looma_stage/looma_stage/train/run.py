"""Стадия обучения как задача агента: конфиг, подъём, сообщения, результат.

Что делает процесс в режиме `--engine train`:

  голова (ранг 0)   грузит свой срез и датасет, ведёт `Trainer` через
                    `ChannelTransport`, пишет прогресс в `out/progress.json`,
                    в конце собирает адаптер в `out/adapter/` и `out/result.json`,
                    говорит остальным «конец» и выходит с кодом 0 — задача
                    закончена;
  остальные         грузят срез и отвечают на сообщения головы; по
                    `train_finish` выходят.

Всё, что здесь, — обвязка: сам счёт в `stage.py`, цикл в `head.py`. Сервер
(`server.py`) отдаёт сюда HTTP-сообщения по видам `train_*` и своё `relay`,
ничего больше о обучении не зная.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from looma_stage.train import data as data_mod
from looma_stage.train import lora as lora_mod
from looma_stage.train.head import Schedule, StepReport, Trainer
from looma_stage.train.stage import OptimSettings, TrainStage, TrainRefused
from looma_stage.train.transport import ChannelTransport, Parts, parted, reply_for

logger = logging.getLogger("looma_stage.train.run")

#: Сколько последних шагов держать в progress.json: графику loss хватает,
#: а файл остаётся маленьким при десятках тысяч шагов.
HISTORY = 500


@dataclass(frozen=True)
class TrainConfig:
    """Всё, что клиент сказал про обучение. Едет задаче файлом `train.json`
    во входах — форма оркестратора пишет его, стадия читает."""

    base_model: str
    dataset: str = "train.jsonl"           # файл во входах или путь
    max_len: int = 2048
    precision: str = "bf16"                # bf16 | nf4
    lora: lora_mod.LoraSettings = lora_mod.LoraSettings()
    optim: OptimSettings = OptimSettings()
    schedule: Schedule = Schedule()
    checkpointing: bool = True
    label: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "TrainConfig":
        raw = raw or {}
        schedule = dict(raw.get("schedule") or {})
        optim = dict(raw.get("optim") or {})
        # lr живёт в расписании (разогрев/затухание), оптимизатору — та же.
        if "lr" in schedule and "lr" not in optim:
            optim["lr"] = schedule["lr"]
        return cls(
            base_model=str(raw.get("base_model") or ""),
            dataset=str(raw.get("dataset") or "train.jsonl"),
            max_len=int(raw.get("max_len") or 2048),
            precision=str(raw.get("precision") or "bf16").lower(),
            lora=lora_mod.LoraSettings.from_dict(raw.get("lora")),
            optim=OptimSettings.from_dict(optim),
            schedule=Schedule.from_dict(schedule),
            checkpointing=bool(raw.get("checkpointing", True)),
            label=str(raw.get("label") or ""),
        )

    @classmethod
    def load(cls, path: str) -> "TrainConfig":
        with open(path, encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


def _quantize(shard, precision: str) -> None:
    """NF4 для базы среза. Отдельная точка, чтобы подмена была одна.

    Не проверено на карте: `bitsandbytes` — только CUDA. Здесь ровно то, что
    делает transformers при `load_in_4bit`: каждый `nn.Linear` слоя
    заменяется `bnb.nn.Linear4bit` с теми же весами; адаптеры вешаются уже
    поверх (`lora.attach` оборачивает `Linear4bit` так же, как `Linear`, —
    ему важны только `in_features`/`out_features` и вызов).
    """
    if precision in ("", "bf16", "bfloat16", "fp16", "float16", "fp32", "float32"):
        return
    if precision != "nf4":
        raise TrainRefused(f"точность базы {precision!r} не поддерживается: bf16 или nf4")
    try:
        import bitsandbytes as bnb
    except ImportError:
        raise TrainRefused(
            "для nf4 нужен bitsandbytes, а его нет в окружении (он только для "
            "CUDA). Берите bf16 или ставьте пакет") from None
    import torch
    from torch import nn

    replaced = 0
    for layer in shard.layers:
        for parent in list(layer.modules()):
            for name, child in list(parent.named_children()):
                if not isinstance(child, nn.Linear):
                    continue
                made = bnb.nn.Linear4bit(child.in_features, child.out_features,
                                         bias=child.bias is not None,
                                         compute_dtype=shard.dtype,
                                         quant_type="nf4", compress_statistics=True)
                made.weight = bnb.nn.Params4bit(child.weight.data.cpu(), requires_grad=False,
                                                quant_type="nf4", compress_statistics=True)
                if child.bias is not None:
                    made.bias = nn.Parameter(child.bias.data.clone(), requires_grad=False)
                made.to(child.weight.device)
                setattr(parent, name, made)
                replaced += 1
    torch.cuda.empty_cache() if torch.cuda.is_available() else None
    logger.info("база среза сжата в NF4: %d модулей", replaced)


class Progress:
    """`out/progress.json`: то, что оркестратор показывает как ход обучения.

    Пишется атомарно (временный файл + rename): читать его будут в любой
    момент, и полфайла — это не «нет прогресса», а сломанный JSON.
    """

    def __init__(self, path: Path, *, total_steps: int) -> None:
        self.path = path
        self.total_steps = total_steps
        self.history: List[dict] = []
        self.started = time.time()
        self.state = "running"
        self.error = ""

    def on_report(self, report: StepReport) -> None:
        self.history.append(report.as_dict())
        del self.history[:-HISTORY]
        self.write()

    def write(self, **extra) -> None:
        last = self.history[-1] if self.history else {}
        done = int(last.get("step") or 0)
        elapsed = time.time() - self.started
        eta = (elapsed / done * (self.total_steps - done)) if done else None
        payload = {
            "state": self.state, "error": self.error,
            "step": done, "total_steps": self.total_steps,
            "epoch": last.get("epoch"), "loss": last.get("loss"),
            "lr": last.get("lr"), "tokens_per_s": last.get("tokens_per_s"),
            "elapsed_s": elapsed, "eta_s": eta,
            "history": self.history, **extra,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False))
        os.replace(temporary, self.path)


class TrainingRun:
    """Живёт в `STATE["train"]` процесса стадии."""

    def __init__(self, *, shard, config: TrainConfig, rank: int, size: int,
                 send: Callable[[dict], None], out_dir: str, work_dir: str,
                 tokenizer=None, on_done: Optional[Callable[[int], None]] = None) -> None:
        self.config = config
        self.rank = rank
        self.size = size
        # Большие сообщения (кусок адаптера, длинные микробатчи) — частями:
        # см. transport.PART_BYTES.
        self.send = parted(send)
        self.parts = Parts()
        self.out_dir = Path(out_dir)
        self.work_dir = Path(work_dir)
        self.tokenizer = tokenizer
        self.on_done = on_done or (lambda code: None)
        _quantize(shard, config.precision)
        self.stage = TrainStage(shard, lora=config.lora, optim=config.optim,
                                checkpointing=config.checkpointing, out_root=out_dir)
        self.transport: Optional[ChannelTransport] = None
        self.thread: Optional[threading.Thread] = None
        if rank == 0:
            self.transport = ChannelTransport(self.stage, size=size, send=self.send)

    # ---------------------------------------------------------- сообщения
    def on_message(self, message: dict) -> None:
        """Сообщение `train_*` с потока приёма."""
        message = self.parts.absorb(message)
        if message is None:
            return          # кусок; целое придёт, когда соберётся
        kind = message.get("kind")
        if kind == "train_reply":
            if self.transport is None:
                logger.warning("ответ стадии пришёл не голове")
                return
            self.transport.deliver(message)
            return
        if kind == "train_finish":
            logger.info("голова сказала: обучение окончено")
            self.on_done(0)
            return
        if self.rank == 0:
            logger.error("голове пришло %s — адресация конвейера сбита", kind)
            return
        self.send(reply_for(self.stage, message))

    # ---------------------------------------------------------------- цикл
    def start(self) -> None:
        """Голова: обучение на отдельном потоке — приём сообщений не должен
        ждать конца эпохи."""
        if self.rank != 0:
            return
        self.thread = threading.Thread(target=self._run, name="train-head", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        code = 0
        progress = Progress(self.out_dir / "progress.json", total_steps=0)
        try:
            examples = self._examples()
            self.transport.wait_ready()
            trainer = Trainer(self.transport, schedule=self.config.schedule,
                              pad_id=self._pad_id(), on_report=progress.on_report,
                              checkpoint_dir="checkpoints")
            import math

            progress.total_steps = (math.ceil(len(examples) / self.config.schedule.batch_size)
                                    * self.config.schedule.epochs)
            progress.write()
            reports = trainer.fit(examples)
            where = trainer.collect_adapter(settings=self.config.lora,
                                            base_model=self.config.base_model,
                                            directory=str(self.out_dir / "adapter"))
            result = {"adapter": str(where.relative_to(self.out_dir)),
                      "steps": len(reports),
                      "final_loss": reports[-1].loss if reports else None,
                      "base_model": self.config.base_model,
                      "precision": self.config.precision,
                      "lora": {"r": self.config.lora.r, "alpha": self.config.lora.alpha,
                               "targets": list(self.config.lora.targets)}}
            (self.out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            progress.state = "done"
            progress.write(result=result)
            logger.info("обучение окончено: %d шагов, адаптер в %s", len(reports), where)
        except Exception as exc:
            logger.exception("обучение упало")
            progress.state, progress.error = "failed", f"{type(exc).__name__}: {exc}"
            progress.write()
            code = 1
        finally:
            if self.transport is not None:
                self.transport.finish()
            self.on_done(code)

    def _examples(self) -> List[data_mod.Example]:
        path = Path(self.config.dataset)
        if not path.is_absolute():
            path = self.work_dir / path
        if not path.exists():
            raise TrainRefused(f"датасета нет: {path}")
        return data_mod.examples_from_jsonl(path, self.tokenizer, max_len=self.config.max_len)

    def _pad_id(self) -> int:
        tokenizer = self.tokenizer
        for name in ("pad_token_id", "eos_token_id"):
            value = getattr(tokenizer, name, None)
            if isinstance(value, int):
                return value
        return 0
