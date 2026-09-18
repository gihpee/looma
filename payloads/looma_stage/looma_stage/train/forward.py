"""Батч через срез слоёв — с паддингом, маской и пересчётом активаций.

Исполнитель инференса (`executor.py`) считает по одной последовательности
без маски: там это честно, батч ему не нужен. Обучению нужен настоящий
батч — несколько примеров разной длины одним тензором, — а значит паддинг
и маска, которая не даёт токену видеть ни будущее, ни чужой паддинг.

Маска строится здесь, а не берётся у модели: слои зовутся напрямую, минуя
`model.forward`, где transformers строит её сам. Форма — та, которую
принимают сами слои: `[B, 1, S, S]`, аддитивная (0 — можно, -inf — нельзя),
в типе модели. Её понимают и eager, и sdpa; flash-attention ждёт двумерную
— ему отдаётся она.

Пересчёт активаций (`gradient checkpointing`) по слоям: на forward
запоминается только вход слоя, на backward слой считается заново. Плата —
около трети счёта, выигрыш — память под графы `m` микробатчей в полёте,
без которого стадия 70B на 24 ГБ не соберётся.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("looma_stage.train.forward")


def causal_mask(attention_mask, *, dtype, device):
    """`[B, S]` (1 — токен, 0 — паддинг) → `[B, 1, S, S]` аддитивная.

    Разрешено, если ключ не позже запроса И ключ не паддинг. Строки
    паддинга (запрос — паддинг) остаются с разрешённой диагональю: полностью
    запрещённая строка даёт NaN в softmax, а NaN в активациях — это NaN в
    градиентах у всего батча.
    """
    import torch

    batch, length = attention_mask.shape
    keys = attention_mask.to(device=device, dtype=torch.bool)[:, None, None, :]
    causal = torch.ones(length, length, dtype=torch.bool, device=device).tril()[None, None]
    allowed = causal & keys
    eye = torch.eye(length, dtype=torch.bool, device=device)[None, None]
    allowed = allowed | eye
    mask = torch.zeros(batch, 1, length, length, dtype=dtype, device=device)
    return mask.masked_fill(~allowed, torch.finfo(dtype).min)


def _uses_flash(shard) -> bool:
    kind = str(getattr(shard.config, "_attn_implementation", "") or "")
    return kind.startswith("flash")


def run_layers(shard, hidden, *, attention_mask, position_ids,
               checkpointing: bool = True):
    """Прогнать `hidden [B, S, H]` через слои среза. Возвращает `[B, S, H]`.

    Слои могут лежать на разных картах узла — состояние, позиции и маска
    переезжают вместе с ними, как у инференса.
    """
    import torch
    from torch.utils.checkpoint import checkpoint

    devices = shard.layer_devices or [shard.device] * len(shard.layers)
    here = devices[0]
    hidden = hidden.to(here)
    positions = position_ids.to(here)
    mask = (attention_mask.to(here) if _uses_flash(shard)
            else causal_mask(attention_mask, dtype=hidden.dtype, device=here))
    rotary = shard.rotary
    embeddings = rotary(hidden, positions) if rotary is not None else None

    for layer, device in zip(shard.layers, devices):
        if device != here:
            hidden, positions, mask = hidden.to(device), positions.to(device), mask.to(device)
            if embeddings is not None:
                embeddings = tuple(e.to(device) for e in embeddings)
            here = device
        if checkpointing and torch.is_grad_enabled():
            hidden = checkpoint(_call_layer, layer, hidden, mask, positions,
                                embeddings, use_reentrant=False)
        else:
            hidden = _call_layer(layer, hidden, mask, positions, embeddings)
    return hidden


def _call_layer(layer, hidden, mask, positions, embeddings):
    out = layer(hidden, attention_mask=mask, position_ids=positions,
                position_embeddings=embeddings, use_cache=False)
    return out[0] if isinstance(out, tuple) else out


def loss_on(shard, hidden, labels, *, scale: float = 1.0):
    """Cross-entropy по следующему токену на последней стадии.

    Возвращает `(loss, число учтённых токенов)`. Метка `-100` — не считать
    (промпт и паддинг). `scale` — доля этого микробатча в батче: loss
    микробатча — среднее по его токенам, а градиент должен сложиться в
    среднее по всему батчу; голова знает общее число токенов и передаёт
    долю.
    """
    import torch
    from torch.nn import functional as F

    device = shard.devices[-1]
    hidden = hidden.to(device)
    hidden = shard.norm(hidden)
    logits = shard.lm_head(hidden[:, :-1, :]).float()
    targets = labels.to(device)[:, 1:]
    counted = int((targets != -100).sum().item())
    if counted == 0:
        # Ни одного токена под loss — так бывает, когда весь ответ обрезался
        # длиной. Нулевой loss с графом, чтобы backward прошёл и не оставил
        # стадии без градиента, которого ждут.
        return logits.sum() * 0.0, 0
    loss = F.cross_entropy(logits.reshape(-1, logits.shape[-1]), targets.reshape(-1),
                           ignore_index=-100, reduction="mean")
    return loss * float(scale), counted
