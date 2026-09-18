"""Обучение по конвейеру равно обучению целой модели.

Главный тест фазы: крошечная Llama на процессоре, один и тот же батч, одни
и те же seed'ы — конвейер из двух стадий обязан дать тот же loss и те же
адаптеры, что одна стадия с целой моделью. Пока это не сходится, стенда нет:
расхождение на 8B на двух узлах выглядело бы как «модель чему-то учится, но
не тому», и по loss этого не увидеть.
"""

from __future__ import annotations

import json

import pytest
import torch

from looma_stage.loader import ShardSpec, build_shard
from looma_stage.train import data as data_mod
from looma_stage.train import lora as lora_mod
from looma_stage.train.head import Schedule, Trainer
from looma_stage.train.stage import TrainStage
from looma_stage.train.transport import LocalTransport

VOCAB, HIDDEN, LAYERS = 64, 32, 4


@pytest.fixture(scope="module")
def checkpoint(tmp_path_factory):
    """Маленькая Llama, сохранённая как настоящий чекпоинт: config.json +
    safetensors с именами `model.layers.N…` — ровно то, что читает loader."""
    from safetensors.torch import save_file
    from transformers import AutoModelForCausalLM, LlamaConfig

    torch.manual_seed(1234)
    config = LlamaConfig(vocab_size=VOCAB, hidden_size=HIDDEN, intermediate_size=64,
                         num_hidden_layers=LAYERS, num_attention_heads=4,
                         num_key_value_heads=2, max_position_embeddings=64,
                         tie_word_embeddings=False)
    model = AutoModelForCausalLM.from_config(config)
    where = tmp_path_factory.mktemp("tiny-llama")
    config.save_pretrained(where)
    state = {name: tensor.contiguous() for name, tensor in model.state_dict().items()}
    save_file(state, str(where / "model.safetensors"))
    return str(where)


def stage_for(checkpoint, start, end, *, seed, checkpointing=True, dropout=0.0):
    spec = ShardSpec(model_path=checkpoint, start_layer=start, end_layer=end,
                     is_first=start == 0, is_last=end == LAYERS,
                     device="cpu", dtype="float32")
    shard, _config = build_shard(spec)
    torch.manual_seed(seed)
    return TrainStage(shard, lora=lora_mod.LoraSettings(r=4, alpha=8, dropout=dropout),
                      checkpointing=checkpointing)


def examples(count=6, seed=7):
    """Примеры разной длины, с промптом под -100 и ответом под loss."""
    rng = torch.Generator().manual_seed(seed)
    made = []
    for i in range(count):
        length = int(torch.randint(5, 13, (1,), generator=rng))
        prompt = int(torch.randint(1, length - 1, (1,), generator=rng))
        ids = torch.randint(1, VOCAB, (length,), generator=rng).tolist()
        labels = [data_mod.IGNORE] * prompt + ids[prompt:]
        made.append(data_mod.Example(input_ids=ids, labels=labels))
    return made


def adapters_of(stages):
    return lora_mod.merge_pieces(stage.adapter_piece() for stage in stages)


def train(stages, *, steps_batch=4, micro=2, seed=0):
    trainer = Trainer(LocalTransport(stages),
                      schedule=Schedule(epochs=1, batch_size=steps_batch, micro_size=micro,
                                        lr=1e-2, max_grad_norm=0.0, shuffle_seed=seed),
                      pad_id=0)
    return trainer, trainer.fit(examples())


# --------------------------------------------------------------- главное
def test_две_стадии_равны_одной_модели(checkpoint):
    """Тот же loss, те же адаптеры после шагов — побитово с точностью
    float32. Адаптеры инициализируются одним seed в том же порядке слоёв,
    поэтому стартуют одинаково."""
    whole = stage_for(checkpoint, 0, LAYERS, seed=0)
    first = stage_for(checkpoint, 0, 2, seed=0)
    # Продолжить тот же поток случайных чисел: слои 2, 3 у целой модели
    # получили A после слоёв 0, 1.
    second_seed_state = torch.get_rng_state()
    second = stage_for(checkpoint, 2, LAYERS, seed=0)
    torch.set_rng_state(second_seed_state)
    _sync_adapters(whole, [first, second])

    _, alone = train([whole])
    _, split = train([first, second])

    assert len(alone) == len(split) == 2
    for a, b in zip(alone, split):
        assert a.loss == pytest.approx(b.loss, abs=1e-5), (a.loss, b.loss)
        assert a.grad_norm == pytest.approx(b.grad_norm, rel=1e-4)

    ours, theirs = adapters_of([whole]), adapters_of([first, second])
    assert set(ours) == set(theirs)
    for name in ours:
        assert torch.allclose(ours[name], theirs[name], atol=1e-6), name


def _sync_adapters(whole, stages):
    """Один старт у обеих раскладок: адаптеры конвейера берут значения у
    целой модели. Иначе сравнивать нечего — kaiming у каждого свой."""
    state = whole.adapter_piece()
    for stage in stages:
        lora_mod.load_adapter_state(stage.shard, state)


def test_без_пересчёта_активаций_то_же_самое(checkpoint):
    """Чекпоинтинг — только память: результат обязан совпасть с прямым
    backward."""
    plain = stage_for(checkpoint, 0, LAYERS, seed=0, checkpointing=False)
    saving = stage_for(checkpoint, 0, LAYERS, seed=0, checkpointing=True)
    _sync_adapters(plain, [saving])
    _, a = train([plain])
    _, b = train([saving])
    for x, y in zip(a, b):
        assert x.loss == pytest.approx(y.loss, abs=1e-6)
    for name, tensor in adapters_of([plain]).items():
        assert torch.allclose(tensor, adapters_of([saving])[name], atol=1e-6)


def test_три_стадии_тоже(checkpoint):
    whole = stage_for(checkpoint, 0, LAYERS, seed=0)
    stages = [stage_for(checkpoint, 0, 1, seed=0), stage_for(checkpoint, 1, 3, seed=0),
              stage_for(checkpoint, 3, LAYERS, seed=0)]
    _sync_adapters(whole, stages)
    _, alone = train([whole])
    _, split = train(stages)
    for a, b in zip(alone, split):
        assert a.loss == pytest.approx(b.loss, abs=1e-5)
    for name, tensor in adapters_of([whole]).items():
        assert torch.allclose(tensor, adapters_of(stages)[name], atol=1e-6), name


# ------------------------------------------------------------ по существу
def test_loss_падает(checkpoint):
    """Адаптер что-то учит: на выучиваемых данных (один и тот же ответ на
    любой промпт) loss за несколько эпох падает заметно."""
    whole = stage_for(checkpoint, 0, LAYERS, seed=0)
    answer = [11, 13, 17, 19, 23]
    learnable = [data_mod.Example(input_ids=[p, p + 1] + answer,
                                  labels=[-100, -100] + answer)
                 for p in range(1, 9)]
    trainer = Trainer(LocalTransport([whole]),
                      schedule=Schedule(epochs=12, batch_size=8, micro_size=4, lr=3e-2,
                                        max_grad_norm=0.0),
                      pad_id=0)
    reports = trainer.fit(learnable)
    assert reports[-1].loss < reports[0].loss * 0.9, [round(r.loss, 3) for r in reports]


def test_паддинг_не_меняет_результат(checkpoint):
    """Пример, посчитанный в одиночку, и он же в батче с более длинным
    соседом обязаны дать одинаковый loss: паддинг вне маски и вне loss."""
    stage = stage_for(checkpoint, 0, LAYERS, seed=0)
    short = data_mod.Example(input_ids=[3, 5, 7, 9, 11], labels=[-100, 5, 7, 9, 11])
    long = data_mod.Example(input_ids=list(range(1, 13)), labels=[-100] * 3 + list(range(4, 13)))

    alone = data_mod.collate([short], pad_id=0)
    answer_alone = stage.forward("a", 0, **_inputs(alone), loss_scale=1.0)
    stage.optimizer.zero_grad(set_to_none=True)

    together = data_mod.collate([short, long], pad_id=0)
    # Под loss — метки со сдвигом на один: первый токен не предсказывается.
    counted_short = sum(1 for label in short.labels[1:] if label != -100)
    counted_long = sum(1 for label in long.labels[1:] if label != -100)
    counted_total = counted_short + counted_long
    # Вклад короткого в общий loss батча — только его доля.
    answer_pair = stage.forward("b", 0, **_inputs(together), loss_scale=1.0)
    stage.optimizer.zero_grad(set_to_none=True)

    # loss батча = средневзвешенное по токенам; выделить вклад короткого
    # можно, посчитав длинный отдельно.
    answer_long = stage.forward("c", 0, **_inputs(data_mod.collate([long], pad_id=0)),
                                loss_scale=1.0)
    stage.optimizer.zero_grad(set_to_none=True)
    expected = (answer_alone["loss"] * counted_short
                + answer_long["loss"] * counted_long) / counted_total
    assert answer_pair["loss"] == pytest.approx(expected, rel=1e-4)


def _inputs(collated):
    return {"input_ids": collated["input_ids"], "labels": collated["labels"],
            "attention_mask": collated["attention_mask"],
            "position_ids": collated["position_ids"]}


# ----------------------------------------------------------- формат PEFT
def test_адаптер_читается_peft(checkpoint, tmp_path):
    """Наш файл — обычный адаптер PEFT: `peft` грузит его на целую модель и
    считает то же, что наш срез с теми же адаптерами. Это же гарантирует,
    что его возьмут vLLM (`--lora-modules`) и transformers."""
    peft = pytest.importorskip("peft")
    from transformers import AutoModelForCausalLM

    stage = stage_for(checkpoint, 0, LAYERS, seed=0)
    # Чтобы адаптер что-то менял: B у нас нулевые на старте.
    with torch.no_grad():
        for parameter in stage.params:
            parameter.add_(torch.randn_like(parameter) * 0.1)
    settings = stage.lora
    lora_mod.write_adapter(tmp_path / "adapter", stage.adapter_piece(),
                           lora_mod.adapter_config(settings, base_model=checkpoint))
    assert json.loads((tmp_path / "adapter" / "adapter_config.json").read_text())["r"] == 4

    base = AutoModelForCausalLM.from_pretrained(checkpoint, dtype=torch.float32)
    loaded = peft.PeftModel.from_pretrained(base, str(tmp_path / "adapter"))
    ids = torch.tensor([[3, 5, 7, 9, 11, 2]])
    with torch.no_grad():
        theirs = loaded(input_ids=ids).logits

    collated = data_mod.collate([data_mod.Example(ids[0].tolist(), ids[0].tolist())], pad_id=0)
    from looma_stage.train.forward import run_layers

    with torch.no_grad():
        hidden = stage.shard.embed(ids)
        hidden = run_layers(stage.shard, hidden,
                            attention_mask=torch.tensor(collated["attention_mask"]),
                            position_ids=torch.tensor(collated["position_ids"]),
                            checkpointing=False)
        ours = stage.shard.lm_head(stage.shard.norm(hidden))
    assert torch.allclose(ours, theirs, atol=1e-4), (ours - theirs).abs().max()


# ------------------------------------------------------- чекпоинт стадии
def test_стадия_продолжает_с_чекпоинта(checkpoint, tmp_path):
    stage = stage_for(checkpoint, 0, LAYERS, seed=0)
    trainer, _ = train([stage])
    stage.save(tmp_path / "ckpt")
    before = adapters_of([stage])

    fresh = stage_for(checkpoint, 0, LAYERS, seed=1)
    fresh.load(tmp_path / "ckpt")
    assert fresh.steps == 2
    for name, tensor in before.items():
        assert torch.equal(tensor, adapters_of([fresh])[name])


# ---------------------------------------------------------------- отказы
def test_шаг_при_микробатчах_в_полёте_отказ(checkpoint):
    """Иначе шаг прошёл бы с половиной градиента — молча."""
    from looma_stage.train.stage import TrainRefused

    first = stage_for(checkpoint, 0, 2, seed=0)
    collated = data_mod.collate([data_mod.Example([1, 2, 3], [1, 2, 3])], pad_id=0)
    first.forward("b", 0, input_ids=collated["input_ids"],
                  attention_mask=collated["attention_mask"],
                  position_ids=collated["position_ids"])
    assert first.in_flight() == 1
    with pytest.raises(TrainRefused, match="в полёте"):
        first.step()
    with pytest.raises(TrainRefused, match="уже в полёте"):
        first.forward("b", 0, input_ids=collated["input_ids"],
                      attention_mask=collated["attention_mask"],
                      position_ids=collated["position_ids"])


def test_backward_без_forward_отказ(checkpoint):
    from looma_stage.train.stage import TrainRefused

    first = stage_for(checkpoint, 0, 2, seed=0)
    with pytest.raises(TrainRefused, match="не держат"):
        first.backward("нет", 0, torch.zeros(1, 1, HIDDEN))


def test_роли_требуют_своё(checkpoint):
    from looma_stage.train.stage import TrainRefused

    first = stage_for(checkpoint, 0, 2, seed=0)
    last = stage_for(checkpoint, 2, LAYERS, seed=0)
    mask, positions = [[1, 1]], [[0, 1]]
    with pytest.raises(TrainRefused, match="нужны токены"):
        first.forward("x", 0, hidden=torch.zeros(1, 2, HIDDEN),
                      attention_mask=mask, position_ids=positions)
    with pytest.raises(TrainRefused, match="нужны активации"):
        last.forward("x", 0, input_ids=[[1, 2]], attention_mask=mask, position_ids=positions)
    with pytest.raises(TrainRefused, match="нужны метки"):
        last.forward("x", 0, hidden=torch.zeros(1, 2, HIDDEN),
                     attention_mask=mask, position_ids=positions)
