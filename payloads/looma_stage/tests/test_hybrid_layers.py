"""Гибридные и MoE-модели на переносимом движке.

Со стенда, Qwen3-Next-80B на 10 стадий: стадия 0 поднялась и упала на первом
же запросе — `IndexError: list index out of range` из `update_conv_state`.
Слой DeltaNet ждёт, что место под его состояние заведено при создании кэша,
а мы создавали пустой `DynamicCache()`. За ней прятались ещё две:

- типы слоёв брались по локальным номерам, а не по глобальным — видно только
  на стадиях 1..9;
- transformers 5 держит экспертов слоя одним тензором, а чекпоинт хранит их
  по одному; загрузчик по именам оставлял экспертов незаполненными, и стадия
  поднималась «здоровой», считая шум.
"""

from __future__ import annotations

import pytest
import torch

from looma_stage.executor import ShardExecutor
from looma_stage.loader import ShardSpec, build_shard
from looma_stage.scheduler import Sequence

pytest.importorskip("transformers.models.qwen3_next")

LAYERS = 8  # интервал 4: полное внимание у слоёв 3 и 7, остальные — DeltaNet


def _tiny_config(**overrides):
    from transformers import Qwen3NextConfig

    return Qwen3NextConfig(**{**dict(
        vocab_size=64, hidden_size=32, intermediate_size=64, moe_intermediate_size=32,
        num_hidden_layers=LAYERS, num_attention_heads=4, num_key_value_heads=2,
        head_dim=8, full_attention_interval=4,
        linear_num_value_heads=4, linear_num_key_heads=2, linear_key_head_dim=8,
        linear_value_head_dim=8, linear_conv_kernel_dim=4,
        num_experts=4, num_experts_per_tok=2, shared_expert_intermediate_size=32,
        decoder_sparse_step=1, tie_word_embeddings=False,
    ), **overrides})


@pytest.fixture(scope="module")
def checkpoint(tmp_path_factory):
    from transformers import AutoModelForCausalLM

    torch.manual_seed(0)
    model = AutoModelForCausalLM.from_config(_tiny_config()).to(torch.float32).eval()
    where = tmp_path_factory.mktemp("qwen3-next-tiny")
    model.save_pretrained(where, safe_serialization=True)
    return str(where)


def _shard(checkpoint, start, end):
    spec = ShardSpec(model_path=checkpoint, start_layer=start, end_layer=end,
                     is_first=start == 0, is_last=end == LAYERS, device="cpu",
                     dtype="float32")
    return build_shard(spec)[0]


def _whole_logits(checkpoint, ids):
    from transformers import AutoModelForCausalLM

    whole = AutoModelForCausalLM.from_pretrained(checkpoint, dtype=torch.float32).eval()
    with torch.no_grad():
        return whole(torch.tensor([ids])).logits[0]


def test_типы_слоёв_режутся_по_глобальным_номерам(checkpoint):
    """Срез со сдвигом на середину интервала: по локальным номерам 0..3
    attention пришёлся бы на последний слой, а не на второй."""
    shard = _shard(checkpoint, 2, 6)
    assert shard.shard_config.layer_types == [
        "linear_attention", "full_attention",
        "linear_attention", "linear_attention",
    ]
    assert shard.shard_config.num_hidden_layers == 4
    assert shard.layers[0].block_type == "linear_attention"
    assert shard.layers[1].block_type == "full_attention"
    # Типы всей модели не тронуты — по ним считает сосед.
    assert len(shard.config.layer_types) == LAYERS


def test_голова_отвечает_на_префилл_и_декод(checkpoint):
    """Ровно тот путь, что упал на стенде: первый слой — DeltaNet, кэш пуст."""
    executor = ShardExecutor(_shard(checkpoint, 0, 5))
    sequence = Sequence(request_id="q", prompt_ids=[1, 2, 3])
    hidden, logits = executor.step_batch([sequence], first_step=True)
    assert logits is None and tuple(hidden["hidden_states"].shape) == (1, 3, 32)
    sequence.output_ids.append(4)
    hidden, _ = executor.step_batch([sequence], first_step=False)
    assert tuple(hidden["hidden_states"].shape) == (1, 1, 32)


def test_срез_равен_целой_модели(checkpoint):
    """Две стадии подряд дают те же логиты, что модель целиком, и на
    префилле, и на декоде из кэша. Иначе типы слоёв, склейка экспертов или
    состояние DeltaNet разошлись бы молча, а не ошибкой."""
    ids = [1, 2, 3, 5, 8]
    expected = _whole_logits(checkpoint, ids)

    head = ShardExecutor(_shard(checkpoint, 0, 5))
    tail = ShardExecutor(_shard(checkpoint, 5, 8))
    prompt = ids[:-1]
    hidden, _ = head.forward(request_id="q", positions=list(range(len(prompt))),
                             input_ids=prompt)
    _, got = tail.forward(request_id="q", positions=list(range(len(prompt))),
                          hidden=hidden)
    torch.testing.assert_close(got, expected[len(prompt) - 1], atol=1e-5, rtol=1e-5)

    hidden, _ = head.forward(request_id="q", positions=[len(prompt)], input_ids=ids[-1:])
    _, got = tail.forward(request_id="q", positions=[len(prompt)], hidden=hidden)
    torch.testing.assert_close(got, expected[-1], atol=1e-5, rtol=1e-5)


def test_незагруженный_параметр_это_отказ_а_не_шум(checkpoint, tmp_path, monkeypatch):
    """Чекпоинт без части экспертов: раньше стадия поднялась бы и отвечала
    мусором, потому что после to_empty() в памяти лежат конечные числа."""
    import json
    import shutil

    from safetensors.torch import load_file, save_file

    where = tmp_path / "torn"
    shutil.copytree(checkpoint, where)
    tensors = load_file(str(where / "model.safetensors"))
    del tensors["model.layers.1.mlp.experts.2.up_proj.weight"]
    save_file(tensors, str(where / "model.safetensors"), metadata={"format": "pt"})

    with pytest.raises(RuntimeError, match=r"layers\.1\.mlp\.experts\.gate_up_proj \(7 of 8\)"):
        _shard(str(where), 0, 3)
