"""LoRA-адаптеры на срезе слоёв и их сохранение в формате PEFT.

Свои сорок строк вместо `peft`, и это не гордость. `peft` оборачивает целую
модель и ведёт учёт адаптеров по ней; у нас модели нет — есть срез слоёв с
локальными индексами 0..n-1, а имена в адаптере обязаны быть глобальными,
чтобы куски от разных стадий сложились в один файл. Проще положить адаптер
на срез самим и написать его в формате, который читают все — `peft`,
transformers, vLLM (`--lora-modules`), — чем учить `peft` нашему срезу.

Сам LoRA — три строки: `y = W x + (B A x) · α/r`, где `W` заморожена,
`A`, `B` — учатся, `B` в начале нули, так что до первого шага адаптер
не меняет модель.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("looma_stage.train.lora")

#: Куда обычно вешают LoRA у трансформеров-декодеров: проекции внимания и
#: MLP. Имена — как в transformers (Llama, Qwen, Mistral, Gemma…).
DEFAULT_TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj",
                   "gate_proj", "up_proj", "down_proj")


@dataclass(frozen=True)
class LoraSettings:
    r: int = 16
    alpha: int = 32
    dropout: float = 0.05
    targets: Tuple[str, ...] = DEFAULT_TARGETS

    @property
    def scaling(self) -> float:
        return self.alpha / self.r

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "LoraSettings":
        raw = raw or {}
        targets = raw.get("targets") or raw.get("target_modules") or DEFAULT_TARGETS
        return cls(r=int(raw.get("r") or 16), alpha=int(raw.get("alpha") or 32),
                   dropout=float(raw.get("dropout") or 0.0),
                   targets=tuple(targets))


_CLASS = None


def lora_linear_class():
    """Класс собирается внутри функции: базовый тип — из torch, а модуль
    обязан импортироваться и там, где torch не нужен. Один на процесс —
    иначе `isinstance` между двумя вызовами не сошёлся бы."""
    global _CLASS
    if _CLASS is not None:
        return _CLASS
    import torch
    from torch import nn

    class LoRALinear(nn.Module):
        """`nn.Linear` с замороженной базой и обучаемой поправкой рядом.

        Адаптер держится в float32 независимо от базы: обновлять bf16-веса
        оптимизатором значит терять младшие разряды шага, и адаптер
        перестаёт учиться раньше, чем это заметно по loss. Поправка
        считается в float32 и приводится к типу базы на выходе.
        """

        def __init__(self, base: nn.Linear, settings: LoraSettings) -> None:
            super().__init__()
            self.base = base
            for parameter in self.base.parameters():
                parameter.requires_grad_(False)
            device = base.weight.device
            self.r = settings.r
            self.scaling = settings.scaling
            self.lora_A = nn.Parameter(
                torch.empty(settings.r, base.in_features, device=device,
                            dtype=torch.float32))
            self.lora_B = nn.Parameter(
                torch.zeros(base.out_features, settings.r, device=device,
                            dtype=torch.float32))
            # Как у PEFT (`init_lora_weights=True`): A — kaiming, B — нули.
            nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))
            self.dropout = (nn.Dropout(settings.dropout) if settings.dropout > 0
                            else nn.Identity())

        @property
        def in_features(self) -> int:
            return self.base.in_features

        @property
        def out_features(self) -> int:
            return self.base.out_features

        def forward(self, x):
            out = self.base(x)
            delta = (self.dropout(x).to(torch.float32) @ self.lora_A.t()
                     @ self.lora_B.t()) * self.scaling
            return out + delta.to(out.dtype)

        def extra_repr(self) -> str:
            return f"r={self.r}, scaling={self.scaling:g}"

    _CLASS = LoRALinear
    return LoRALinear


def attach(shard, settings: LoraSettings) -> List:
    """Повесить адаптеры на срез и заморозить всё остальное.

    Возвращает список обучаемых параметров — ровно то, что нужно
    оптимизатору. Замораживаются и эмбеддинги с головой: у первой и
    последней стадии они есть, но учить их — уже не LoRA.
    """
    from torch import nn

    LoRALinear = lora_linear_class()
    for module in _stage_modules(shard):
        for parameter in module.parameters():
            parameter.requires_grad_(False)

    params: List = []
    attached = 0
    for layer in shard.layers:
        for parent, name, child in _linears(layer):
            if name not in settings.targets or isinstance(child, LoRALinear):
                continue
            wrapped = LoRALinear(child, settings)
            setattr(parent, name, wrapped)
            params.extend([wrapped.lora_A, wrapped.lora_B])
            attached += 1
    if not attached:
        raise ValueError(
            f"ни один модуль среза не подошёл под {sorted(settings.targets)}; "
            "у этой архитектуры проекции называются иначе")
    logger.info("LoRA: %d модулей, r=%d, α=%d, обучаемых параметров %d",
                attached, settings.r, settings.alpha,
                sum(p.numel() for p in params))
    return params


def _linears(root) -> Iterable[Tuple[object, str, object]]:
    """Все `nn.Linear` внутри модуля: (родитель, имя, модуль)."""
    from torch import nn

    for parent in root.modules():
        for name, child in list(parent.named_children()):
            if isinstance(child, nn.Linear):
                yield parent, name, child


def _stage_modules(shard) -> Iterable:
    for module in (shard.embed, shard.layers, shard.norm, shard.lm_head):
        if module is not None:
            yield module


# ------------------------------------------------------------- формат PEFT
def peft_key(prefix: str, global_layer: int, path: str, which: str) -> str:
    """Имя тензора адаптера так, как его ждут `peft` и vLLM.

    `base_model.model.` — обёртка PeftModel над моделью; дальше путь модуля в
    самой модели: `{prefix}layers.{N}.{путь до Linear}.lora_{A|B}.weight`,
    где `prefix` — то, что чекпоинт ставит перед `layers.` (`model.`,
    у вложенных — `model.language_model.`).
    """
    return f"base_model.model.{prefix}layers.{global_layer}.{path}.lora_{which}.weight"


def adapter_state(shard) -> Dict[str, object]:
    """Тензоры адаптеров этого среза под ГЛОБАЛЬНЫМИ именами.

    Глобальными — чтобы куски от разных стадий легли в один файл без
    переименований и чтобы файл читался кем угодно как обычный адаптер к
    целой модели.
    """
    LoRALinear = lora_linear_class()
    prefix = getattr(shard, "_key_prefix", "model.")
    state: Dict[str, object] = {}
    for local, layer in enumerate(shard.layers):
        global_index = shard.spec.start_layer + local
        for path, module in layer.named_modules():
            if not isinstance(module, LoRALinear):
                continue
            state[peft_key(prefix, global_index, path, "A")] = module.lora_A.detach().cpu()
            state[peft_key(prefix, global_index, path, "B")] = module.lora_B.detach().cpu()
    return state


def load_adapter_state(shard, state: Dict[str, object]) -> int:
    """Обратно: положить тензоры адаптера в модули среза. Возвращает, сколько
    легло. Лишние имена (чужих слоёв) пропускаются — так один файл адаптера
    читает каждая стадия."""
    import torch

    LoRALinear = lora_linear_class()
    prefix = getattr(shard, "_key_prefix", "model.")
    placed = 0
    for local, layer in enumerate(shard.layers):
        global_index = shard.spec.start_layer + local
        for path, module in layer.named_modules():
            if not isinstance(module, LoRALinear):
                continue
            for which, parameter in (("A", module.lora_A), ("B", module.lora_B)):
                tensor = state.get(peft_key(prefix, global_index, path, which))
                if tensor is None:
                    continue
                with torch.no_grad():
                    parameter.copy_(tensor.to(parameter.device, parameter.dtype))
                placed += 1
    return placed


def merge_pieces(pieces: Iterable[Dict[str, object]]) -> Dict[str, object]:
    """Куски адаптера от стадий — в один. Пересечение имён — отказ: два
    куска для одного слоя означают, что срезы стадий перекрылись."""
    merged: Dict[str, object] = {}
    for piece in pieces:
        overlap = set(piece) & set(merged)
        if overlap:
            raise ValueError(f"адаптер собран с пересечением: {sorted(overlap)[:3]}")
        merged.update(piece)
    return merged


def adapter_config(settings: LoraSettings, *, base_model: str) -> dict:
    """`adapter_config.json`, который понимают `peft`, transformers и vLLM."""
    return {
        "peft_type": "LORA",
        "task_type": "CAUSAL_LM",
        "base_model_name_or_path": base_model,
        "r": settings.r,
        "lora_alpha": settings.alpha,
        "lora_dropout": settings.dropout,
        "target_modules": sorted(settings.targets),
        "bias": "none",
        "fan_in_fan_out": False,
        "init_lora_weights": True,
        "use_rslora": False,
        "use_dora": False,
        "inference_mode": False,
    }


def write_adapter(directory, state: Dict[str, object], config: dict) -> Path:
    """Сложить адаптер на диск как PEFT: `adapter_config.json` +
    `adapter_model.safetensors`."""
    from safetensors.torch import save_file

    where = Path(directory)
    where.mkdir(parents=True, exist_ok=True)
    tensors = {name: tensor.contiguous() for name, tensor in state.items()}
    save_file(tensors, str(where / "adapter_model.safetensors"))
    (where / "adapter_config.json").write_text(json.dumps(config, indent=2))
    logger.info("адаптер записан: %s (%d тензоров)", where, len(tensors))
    return where


def read_adapter(directory) -> Tuple[Dict[str, object], dict]:
    from safetensors.torch import load_file

    where = Path(directory)
    state = load_file(str(where / "adapter_model.safetensors"))
    config = json.loads((where / "adapter_config.json").read_text())
    return state, config
