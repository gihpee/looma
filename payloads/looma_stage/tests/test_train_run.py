"""Стадия обучения как задача: конфиг, сообщения по каналу, результат.

Две стадии в одном процессе, связанные так, как их связал бы агент: голова
шлёт сообщение с `target_stage`, «канал» относит его на другой поток, стадия
отвечает `train_reply`. Итог обязан совпасть с обучением в одном процессе.
"""

from __future__ import annotations

import json
import queue
import threading

import pytest
import torch

from looma_stage.loader import ShardSpec, build_shard
from looma_stage.train import lora as lora_mod
from looma_stage.train.head import Schedule, Trainer
from looma_stage.train.run import TrainConfig, TrainingRun
from looma_stage.train.stage import TrainStage
from looma_stage.train.transport import LocalTransport
from tests.test_train_pipeline import LAYERS, checkpoint, examples  # noqa: F401


class _Tokenizer:
    pad_token_id = 0
    eos_token_id = 2
    chat_template = None


class _Channel:
    """Что агент делает для стадий: возит словари по рангу, не глядя внутрь."""

    def __init__(self) -> None:
        self.runs = {}
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def send(self, message: dict) -> None:
        self.inbox.put(json.loads(json.dumps(message)))     # как по проводу

    def _pump(self) -> None:
        while True:
            message = self.inbox.get()
            self.runs[int(message["target_stage"])].on_message(message)


def _config(tmp_path, **schedule):
    return TrainConfig.from_dict({
        "base_model": "tiny/llama", "dataset": str(tmp_path / "train.jsonl"),
        "max_len": 32, "precision": "bf16",
        "lora": {"r": 4, "alpha": 8, "dropout": 0.0},
        "schedule": {"epochs": 1, "batch_size": 4, "micro_size": 2, "lr": 1e-2,
                     "max_grad_norm": 0.0, "shuffle_seed": 0, **schedule},
    })


def _dataset(tmp_path):
    rows = [{"input_ids": e.input_ids, "labels": e.labels} for e in examples()]
    (tmp_path / "train.jsonl").write_text("\n".join(json.dumps(r) for r in rows))


def _settled(codes, size=2, timeout_s=10.0):
    """`train_finish` уезжает без ожидания, канал относит его на своём потоке:
    исход хвоста появляется чуть позже, чем выходит голова."""
    import time

    deadline = time.monotonic() + timeout_s
    while len(codes) < size and time.monotonic() < deadline:
        time.sleep(0.02)
    return codes


def _shard(checkpoint, start, end):
    spec = ShardSpec(model_path=checkpoint, start_layer=start, end_layer=end,
                     is_first=start == 0, is_last=end == LAYERS, device="cpu",
                     dtype="float32")
    return build_shard(spec)[0]


def test_две_задачи_через_канал_равны_одному_процессу(checkpoint, tmp_path):
    _dataset(tmp_path)
    config = _config(tmp_path)
    channel = _Channel()
    codes = {}
    out = {rank: tmp_path / f"out-{rank}" for rank in (0, 1)}

    torch.manual_seed(0)
    head = TrainingRun(shard=_shard(checkpoint, 0, 2), config=config, rank=0, size=2,
                       send=channel.send, out_dir=str(out[0]), work_dir=str(tmp_path),
                       tokenizer=_Tokenizer(), on_done=lambda c: codes.__setitem__(0, c))
    tail = TrainingRun(shard=_shard(checkpoint, 2, LAYERS), config=config, rank=1, size=2,
                       send=channel.send, out_dir=str(out[1]), work_dir=str(tmp_path),
                       on_done=lambda c: codes.__setitem__(1, c))
    channel.runs = {0: head, 1: tail}

    # Эталон: те же адаптеры на старте, обучение в одном процессе.
    torch.manual_seed(0)
    whole = TrainStage(_shard(checkpoint, 0, LAYERS), lora=config.lora, optim=config.optim)
    state = lora_mod.merge_pieces([head.stage.adapter_piece(), tail.stage.adapter_piece()])
    lora_mod.load_adapter_state(whole.shard, state)
    reference = Trainer(LocalTransport([whole]), schedule=config.schedule, pad_id=0)
    expected = reference.fit(examples())

    head.start()
    head.thread.join(timeout=120)
    assert not head.thread.is_alive(), "голова не закончила"
    assert _settled(codes) == {0: 0, 1: 0}, codes

    progress = json.loads((out[0] / "progress.json").read_text())
    assert progress["state"] == "done" and progress["step"] == len(expected)
    for got, want in zip(progress["history"], expected):
        assert got["loss"] == pytest.approx(want.loss, abs=1e-5)

    result = json.loads((out[0] / "result.json").read_text())
    assert result["adapter"] == "adapter"
    merged, _config_json = lora_mod.read_adapter(out[0] / "adapter")
    for name, tensor in whole.adapter_piece().items():
        assert torch.allclose(merged[name], tensor, atol=1e-6), name


def test_большие_сообщения_едут_кусками(checkpoint, tmp_path, monkeypatch):
    """Со стенда: кусок адаптера стадии (20 МБ) не доезжал до головы —
    `train_collect` ждал 600 с и падал. Канал здесь роняет всё крупнее
    порога, как gRPC-путь агента; порог занижен, чтобы даже адаптер
    крошечной модели пришлось резать. Обучение обязано дойти до адаптера,
    а на провод не должно попасть ни одного цельного большого сообщения."""
    from looma_stage.train import transport as transport_mod

    _dataset(tmp_path)
    config = _config(tmp_path)
    monkeypatch.setattr(transport_mod, "PART_BYTES", 4096)
    limit = 4096 * 2      # часть + обёртка влезают, цельный адаптер — нет

    class _Strict(_Channel):
        def __init__(self) -> None:
            super().__init__()
            self.parts = 0
            self.largest = 0

        def send(self, message: dict) -> None:
            size = len(json.dumps(message))
            self.largest = max(self.largest, size)
            if size > limit:
                return                       # молча, как настоящий провод
            if message.get("kind") != "train_part":
                super().send(message)
                return
            self.parts += 1
            # Куски приходят не по порядку — как при откате с прямого пути
            # на ретранслятор: чётный придерживаем, отдаём после следующего.
            last = message["index"] == message["total"] - 1
            if message["index"] % 2 == 0 and not last:
                self.held = message
                return
            super().send(message)
            if getattr(self, "held", None) is not None:
                super().send(self.held)
                self.held = None

    channel = _Strict()
    codes = {}
    out = {rank: tmp_path / f"out-{rank}" for rank in (0, 1)}
    head = TrainingRun(shard=_shard(checkpoint, 0, 2), config=config, rank=0, size=2,
                       send=channel.send, out_dir=str(out[0]), work_dir=str(tmp_path),
                       tokenizer=_Tokenizer(), on_done=lambda c: codes.__setitem__(0, c))
    tail = TrainingRun(shard=_shard(checkpoint, 2, LAYERS), config=config, rank=1, size=2,
                       send=channel.send, out_dir=str(out[1]), work_dir=str(tmp_path),
                       on_done=lambda c: codes.__setitem__(1, c))
    channel.runs = {0: head, 1: tail}
    head.start()
    head.thread.join(timeout=120)
    assert _settled(codes) == {0: 0, 1: 0}, codes
    assert channel.parts > 1 and channel.largest <= limit, (channel.parts, channel.largest)
    merged, _ = lora_mod.read_adapter(out[0] / "adapter")
    assert any(".layers.3." in name for name in merged)   # кусок хвоста доехал


def test_куски_собираются_в_любом_порядке():
    from looma_stage.train.transport import Parts, parted

    sent = []
    parted(sent.append)({"kind": "train_reply", "target_stage": 0, "answer": {"x": "y" * 3_000_000}})
    assert len(sent) > 2 and all(m["kind"] == "train_part" for m in sent)
    parts = Parts()
    whole = None
    for message in reversed(sent):
        whole = parts.absorb(message)
    assert whole == {"kind": "train_reply", "target_stage": 0, "answer": {"x": "y" * 3_000_000}}
    assert parts.absorb({"kind": "train_ping"}) == {"kind": "train_ping"}


def test_чекпоинты_каждой_стадии_в_свой_каталог(checkpoint, tmp_path):
    _dataset(tmp_path)
    config = _config(tmp_path, save_every=1)
    channel = _Channel()
    out = {rank: tmp_path / f"out-{rank}" for rank in (0, 1)}
    head = TrainingRun(shard=_shard(checkpoint, 0, 2), config=config, rank=0, size=2,
                       send=channel.send, out_dir=str(out[0]), work_dir=str(tmp_path),
                       tokenizer=_Tokenizer())
    tail = TrainingRun(shard=_shard(checkpoint, 2, LAYERS), config=config, rank=1, size=2,
                       send=channel.send, out_dir=str(out[1]), work_dir=str(tmp_path))
    channel.runs = {0: head, 1: tail}
    head.start()
    head.thread.join(timeout=120)
    for rank in (0, 1):
        assert (out[rank] / "checkpoints" / "step-2" / "adapter_piece.safetensors").exists()


def test_ошибка_стадии_доходит_до_головы(checkpoint, tmp_path):
    """Стадия упала — голова узнаёт причину сразу, а не по таймауту, и
    закрывает обучение как провал."""
    _dataset(tmp_path)
    config = _config(tmp_path)
    channel = _Channel()
    codes = {}
    out = tmp_path / "out"
    head = TrainingRun(shard=_shard(checkpoint, 0, 2), config=config, rank=0, size=2,
                       send=channel.send, out_dir=str(out), work_dir=str(tmp_path),
                       tokenizer=_Tokenizer(), on_done=lambda c: codes.__setitem__(0, c))
    tail = TrainingRun(shard=_shard(checkpoint, 2, LAYERS), config=config, rank=1, size=2,
                       send=channel.send, out_dir=str(tmp_path / "out1"), work_dir=str(tmp_path))
    tail.stage.forward = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("карта отвалилась"))
    channel.runs = {0: head, 1: tail}
    head.start()
    head.thread.join(timeout=60)
    assert codes.get(0) == 1
    progress = json.loads((out / "progress.json").read_text())
    assert progress["state"] == "failed" and "карта отвалилась" in progress["error"]


def test_конфиг_читается_целиком():
    made = TrainConfig.from_dict({"base_model": "a/b", "precision": "NF4",
                                  "schedule": {"lr": 5e-4, "epochs": 3},
                                  "lora": {"r": 8}})
    assert made.precision == "nf4" and made.schedule.epochs == 3
    assert made.optim.lr == 5e-4 and made.lora.r == 8


def test_nf4_без_bitsandbytes_отказ_вслух(checkpoint, tmp_path, monkeypatch):
    import sys

    from looma_stage.train.stage import TrainRefused

    monkeypatch.setitem(sys.modules, "bitsandbytes", None)
    with pytest.raises(TrainRefused, match="bitsandbytes"):
        TrainingRun(shard=_shard(checkpoint, 0, LAYERS),
                    config=TrainConfig.from_dict({"base_model": "x", "precision": "nf4"}),
                    rank=0, size=1, send=lambda m: None, out_dir=str(tmp_path),
                    work_dir=str(tmp_path))
