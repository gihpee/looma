"""Диалоги → токены и метки. Токенизатор — заглушка с шаблоном чата:
проверяется наша логика границы loss, а не чужой Jinja."""

from __future__ import annotations

import json

import pytest

from looma_stage.train import data as data_mod
from looma_stage.train import lora as lora_mod


class _Tokenizer:
    """Шаблон: `<role>` + символы текста; ответ ассистента ждут после
    `<assistant>`. Токены — коды символов, роли — 200+."""

    chat_template = "fake"
    roles = {"system": 200, "user": 201, "assistant": 202}

    def apply_chat_template(self, messages, *, tokenize=True, add_generation_prompt=False):
        ids = []
        for message in messages:
            ids.append(self.roles[message["role"]])
            ids.extend(ord(ch) for ch in message["content"])
        if add_generation_prompt:
            ids.append(self.roles["assistant"])
        return ids


def test_loss_только_по_ответу():
    example = data_mod.encode_dialog(
        _Tokenizer(), [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
        max_len=64)
    assert example.input_ids == [201, ord("h"), ord("i"), 202, ord("y"), ord("o")]
    assert example.labels == [-100, -100, -100, -100, ord("y"), ord("o")]
    assert example.trainable == 2


def test_обрезка_по_длине():
    example = data_mod.encode_dialog(
        _Tokenizer(), [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yoyo"}],
        max_len=5)
    assert len(example) == 5 and example.trainable == 1


def test_последним_должен_быть_ассистент():
    with pytest.raises(data_mod.DataRefused, match="assistant"):
        data_mod.encode_dialog(_Tokenizer(), [{"role": "user", "content": "hi"}], max_len=8)


def test_без_шаблона_чата_отказ():
    class Bare:
        chat_template = None

    with pytest.raises(data_mod.DataRefused, match="chat_template.jinja"):
        data_mod.encode_dialog(Bare(), [{"role": "assistant", "content": "x"}], max_len=8)


def test_jsonl_целиком_и_отброс_пустых(tmp_path, caplog):
    path = tmp_path / "d.jsonl"
    rows = [{"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "bcd"}]},
            {"messages": [{"role": "user", "content": "long prompt"}, {"role": "assistant", "content": "z"}]}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    examples = data_mod.examples_from_jsonl(path, _Tokenizer(), max_len=6)
    # Второй: промпт из 12 токенов не оставляет места ответу — отброшен.
    assert len(examples) == 1 and examples[0].trainable == 3


def test_склейка_с_паддингом():
    a = data_mod.Example(input_ids=[1, 2, 3], labels=[-100, 2, 3])
    b = data_mod.Example(input_ids=[4], labels=[4])
    collated = data_mod.collate([a, b], pad_id=0)
    assert collated["input_ids"] == [[1, 2, 3], [4, 0, 0]]
    assert collated["labels"] == [[-100, 2, 3], [4, -100, -100]]
    assert collated["attention_mask"] == [[1, 1, 1], [1, 0, 0]]
    assert collated["position_ids"] == [[0, 1, 2], [0, 1, 2]]


def test_батчи_режутся_на_микробатчи():
    examples = [data_mod.Example([i], [i]) for i in range(5)]
    batches = list(data_mod.batches(examples, batch_size=4, micro_size=2, shuffle_seed=None))
    assert [[len(m) for m in b] for b in batches] == [[2, 2], [1]]


# ---------------------------------------------------------------- адаптер
def test_куски_адаптера_не_должны_пересекаться():
    with pytest.raises(ValueError, match="пересечением"):
        lora_mod.merge_pieces([{"x": 1}, {"x": 2}])


def test_имя_в_формате_peft():
    assert (lora_mod.peft_key("model.", 17, "self_attn.q_proj", "A")
            == "base_model.model.model.layers.17.self_attn.q_proj.lora_A.weight")


def test_настройки_из_словаря():
    made = lora_mod.LoraSettings.from_dict({"r": 8, "alpha": 16, "target_modules": ["q_proj"]})
    assert (made.r, made.alpha, made.targets, made.scaling) == (8, 16, ("q_proj",), 2.0)
