"""Стадия в режиме обучения: forward, backward, шаг, сохранение.

Один класс на все роли. Первая стадия начинает с токенов (эмбеддинги), любая
другая — с пришедших активаций; последняя считает loss и делает первый
backward, остальные ждут градиент по своему выходу и отдают градиент по
входу назад. Стадия, которая и первая, и последняя, — это одна машина с
целой моделью, и ей ничего отдавать и ждать не надо.

Autograd через границу процесса устроен так: пришедшие активации становятся
листом графа (`requires_grad`), граф удерживается до прихода `backward` по
ключу `(batch_id, micro)`, после `backward` у листа есть `.grad` — он и
уезжает предыдущей стадии, а граф отпускается. Ничего экзотического: это
обычный autograd, у которого «предыдущий слой» живёт на другой машине.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from looma_stage.train import lora as lora_mod
from looma_stage.train.forward import loss_on, run_layers

logger = logging.getLogger("looma_stage.train.stage")


class TrainRefused(RuntimeError):
    """Обучение тут не пойдёт, и вот почему."""


@dataclass(frozen=True)
class OptimSettings:
    lr: float = 2e-4
    weight_decay: float = 0.0
    betas: Tuple[float, float] = (0.9, 0.999)

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "OptimSettings":
        raw = raw or {}
        return cls(lr=float(raw.get("lr") or 2e-4),
                   weight_decay=float(raw.get("weight_decay") or 0.0),
                   betas=tuple(raw.get("betas") or (0.9, 0.999)))


class TrainStage:
    """Срез модели с адаптерами и оптимизатором, готовый принимать шаги."""

    def __init__(self, shard, *, lora: lora_mod.LoraSettings,
                 optim: OptimSettings = OptimSettings(),
                 checkpointing: bool = True, out_root: Optional[str] = None) -> None:
        import torch

        self.torch = torch
        self.shard = shard
        # Куда класть чекпоинты по относительному пути: каталог результатов
        # задачи. Голова шлёт всем один и тот же относительный путь, а
        # каталог у каждой стадии свой.
        self.out_root = Path(out_root) if out_root else None
        self.is_first = shard.spec.is_first
        self.is_last = shard.spec.is_last
        self.lora = lora
        self.checkpointing = checkpointing
        self.params = lora_mod.attach(shard, lora)
        self.optimizer = torch.optim.AdamW(self.params, lr=optim.lr,
                                           weight_decay=optim.weight_decay,
                                           betas=optim.betas)
        # Графы микробатчей в полёте: (batch_id, micro) -> (вход, выход).
        self._held: Dict[Tuple[str, int], Tuple[object, object]] = {}
        self.steps = 0

    # -------------------------------------------------------------- forward
    def forward(self, batch_id: str, micro: int, *, attention_mask, position_ids,
                input_ids=None, hidden=None, labels=None, loss_scale: float = 1.0) -> dict:
        """Один микробатч вперёд.

        Возвращает словарь: у неголовной последней — `grad` по входу и
        `loss`; у последней-и-первой — только `loss`; у остальных —
        `hidden` для следующей стадии.
        """
        torch = self.torch
        key = (batch_id, int(micro))
        if key in self._held:
            raise TrainRefused(f"микробатч {key} уже в полёте: backward по нему не приходил")

        if self.is_first:
            if input_ids is None:
                raise TrainRefused("первой стадии нужны токены, а не активации")
            ids = torch.as_tensor(input_ids, dtype=torch.long, device=self.shard.devices[0])
            entry = self.shard.embed(ids)
            leaf = None
        else:
            if hidden is None:
                raise TrainRefused("неголовной стадии нужны активации предыдущей")
            # Лист графа: с него начнётся backward этой стадии, и его .grad —
            # то, что уедет назад.
            leaf = hidden.to(self.shard.devices[0], self.shard.dtype).detach().requires_grad_(True)
            entry = leaf

        mask = torch.as_tensor(attention_mask, dtype=torch.long)
        positions = torch.as_tensor(position_ids, dtype=torch.long)
        out = run_layers(self.shard, entry, attention_mask=mask, position_ids=positions,
                         checkpointing=self.checkpointing)

        if not self.is_last:
            self._held[key] = (leaf, out)
            return {"hidden": out.detach()}

        if labels is None:
            raise TrainRefused("последней стадии нужны метки, чтобы посчитать loss")
        loss, counted = loss_on(self.shard, out, torch.as_tensor(labels, dtype=torch.long),
                                scale=loss_scale)
        loss.backward()
        answer = {"loss": float(loss.detach().item()), "tokens": counted}
        if leaf is not None:
            answer["grad"] = leaf.grad.detach()
        return answer

    # ------------------------------------------------------------- backward
    def backward(self, batch_id: str, micro: int, grad) -> dict:
        """Градиент по выходу пришёл — досчитать до входа и отдать его."""
        key = (batch_id, int(micro))
        held = self._held.pop(key, None)
        if held is None:
            raise TrainRefused(f"backward для {key}, а его forward тут не держат")
        leaf, out = held
        out.backward(grad.to(out.device, out.dtype))
        if leaf is None:
            return {}
        return {"grad": leaf.grad.detach()}

    def in_flight(self) -> int:
        return len(self._held)

    # ----------------------------------------------------------------- шаг
    def grad_norm_squared(self) -> float:
        """Квадрат нормы градиента адаптеров ЭТОЙ стадии.

        Квадрат, а не норма: норма всей модели — корень из суммы квадратов
        по стадиям, и складывать её из корней нельзя. Голова собирает
        квадраты со всех, считает общую норму и присылает один коэффициент
        клиппинга на всех — так обрезка не зависит от того, на сколько
        стадий порезана модель.
        """
        total = 0.0
        for parameter in self.params:
            if parameter.grad is not None:
                total += float(parameter.grad.detach().float().pow(2).sum().item())
        return total

    def step(self, *, lr: Optional[float] = None, grad_scale: float = 1.0) -> dict:
        """Применить накопленные градиенты адаптеров и обнулить их.

        `grad_scale` — коэффициент клиппинга, общий для всех стадий (см.
        `grad_norm_squared`); 1.0 — без обрезки.
        """
        if self._held:
            raise TrainRefused(
                f"шаг при {len(self._held)} микробатчах в полёте: их backward "
                "ещё не пришёл, и градиент неполный")
        if lr is not None:
            for group in self.optimizer.param_groups:
                group["lr"] = float(lr)
        if grad_scale != 1.0:
            with self.torch.no_grad():
                for parameter in self.params:
                    if parameter.grad is not None:
                        parameter.grad.mul_(float(grad_scale))
        self.optimizer.step()
        self.optimizer.zero_grad(set_to_none=True)
        self.steps += 1
        return {"steps": self.steps}

    # ---------------------------------------------------------- сохранение
    def adapter_piece(self) -> Dict[str, object]:
        """Тензоры адаптеров ЭТОГО среза под глобальными именами."""
        return lora_mod.adapter_state(self.shard)

    def save(self, directory) -> Path:
        """Чекпоинт стадии: адаптеры + состояние оптимизатора + счётчик."""
        from safetensors.torch import save_file

        where = self._resolve(directory)
        where.mkdir(parents=True, exist_ok=True)
        piece = {name: tensor.contiguous() for name, tensor in self.adapter_piece().items()}
        save_file(piece, str(where / "adapter_piece.safetensors"))
        self.torch.save({"optimizer": self.optimizer.state_dict(), "steps": self.steps},
                        where / "optimizer.pt")
        return where

    def load(self, directory) -> None:
        from safetensors.torch import load_file

        where = self._resolve(directory)
        placed = lora_mod.load_adapter_state(
            self.shard, load_file(str(where / "adapter_piece.safetensors")))
        state = self.torch.load(where / "optimizer.pt", map_location="cpu")
        self.optimizer.load_state_dict(state["optimizer"])
        self.steps = int(state.get("steps") or 0)
        logger.info("стадия продолжает с шага %d (%d тензоров адаптера)", self.steps, placed)

    def _resolve(self, directory) -> Path:
        where = Path(directory)
        if not where.is_absolute() and self.out_root is not None:
            where = self.out_root / where
        return where
