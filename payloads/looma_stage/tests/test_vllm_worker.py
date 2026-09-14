"""Воркер стадии — то, что vLLM строит в каждом процессе на каждой карте.

vLLM тут нет: его воркер, исполнитель и группа подменены заглушками, которые
запоминают, что и в каком порядке с ними сделали. Проверяется ровно то, чем
наш воркер отличается от штатного: подмены накладываются в каждом, срез
берётся из конфига, шаг принимает тензоры снаружи и отвечает только с
нулевого ранга.
"""

from __future__ import annotations

import sys
import types

import pytest

from looma_stage.vllm_runner import RunnerRefused


# ------------------------------------------------------------ настройки
def _config(stage=None):
    extra = {} if stage is None else {"looma_stage": stage}
    return types.SimpleNamespace(additional_config=extra)


def test_срез_берётся_из_конфига():
    from looma_stage import vllm_worker

    stage = {"start_layer": 12, "end_layer": 24, "num_model_layers": 36}
    assert vllm_worker.settings_of(_config(stage)) == stage


def test_без_среза_в_конфиге_отказ_называет_вход():
    """Воркер, поднятый в обход движка, собрал бы всю модель."""
    from looma_stage import vllm_worker

    with pytest.raises(RunnerRefused, match="looma_stage.vllm_engine"):
        vllm_worker.settings_of(_config())


def test_имя_класса_совпадает_с_тем_что_отдаём_vllm():
    from looma_stage import vllm_worker

    assert vllm_worker.QUALNAME == f"{vllm_worker.__name__}.StageWorker"


def test_без_vllm_класс_не_собирается_но_модуль_живёт():
    from looma_stage import vllm_worker

    with pytest.raises(AttributeError):
        vllm_worker.something_else


# ---------------------------------------------------------- заглушки vLLM
class _Runner:
    """GPUModelRunner: помнит, из чьего init_device построен."""

    made = []

    def __init__(self, vllm_config, device):
        self.vllm_config, self.device = vllm_config, device
        self.model = types.SimpleNamespace(
            model=types.SimpleNamespace(layers=[object()] * 12),
            make_empty_intermediate_tensors=lambda **_k: "буфер")
        self.max_num_tokens, self.model_config = 16, types.SimpleNamespace(dtype="bf16")
        self.executed = []
        self.execute_model_state = None
        _Runner.made.append(self)

    def load_model(self, **_kwargs):
        pass

    def execute_model(self, scheduler_output, intermediate_tensors=None):
        self.executed.append((scheduler_output, intermediate_tensors))
        return self.answer

    answer = None


class _Worker:
    """Штатный воркер vLLM: строит исполнитель в init_device по имени класса
    в модуле — как настоящий."""

    def __init__(self, *, vllm_config, local_rank, rank, distributed_init_method,
                 is_driver_worker=False):
        self.vllm_config = vllm_config
        self.local_rank, self.rank = local_rank, rank
        self.device = f"cuda:{local_rank}"
        self.parallel_config = vllm_config.parallel_config
        self.cache_config = vllm_config.cache_config
        self.trail = []

    def init_device(self):
        import vllm.v1.worker.gpu_model_runner as runners

        self.trail.append("device")
        self.model_runner = runners.GPUModelRunner(self.vllm_config, self.device)

    def load_model(self):
        import vllm.distributed.utils as utils

        self.trail.append("load")
        # Что бы ни насчитал vLLM, грузятся наши слои — проверяем, что
        # подмена стоит именно во время загрузки.
        self.seen_range = utils.get_pp_indices(36, 0, 1)


class _Tensor:
    def __init__(self, name, where="cpu"):
        self.name, self.where = name, where

    def to(self, device, non_blocking=False):
        return _Tensor(self.name, device)

    def cpu(self):
        return _Tensor(self.name, "cpu")

    def clone(self):
        return self

    shape = (1, 7)


class _Intermediate:
    def __init__(self, tensors):
        self.tensors = dict(tensors)

    def items(self):
        return self.tensors.items()


@pytest.fixture
def fake_vllm(monkeypatch):
    """Ровно те модули vLLM, которые трогает воркер."""
    _Runner.made = []
    modules = {}

    def module(name, **attrs):
        made = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(made, key, value)
        modules[name] = made
        return made

    module("vllm")
    module("vllm.v1")
    module("vllm.v1.worker")
    module("vllm.v1.worker.gpu_worker", Worker=_Worker)
    module("vllm.v1.worker.gpu_model_runner", GPUModelRunner=_Runner)
    module("vllm.sequence", IntermediateTensors=_Intermediate)
    module("vllm.distributed")
    module("vllm.distributed.utils",
           get_pp_indices=lambda num_layers, rank, world_size: (0, num_layers))
    state = module("vllm.distributed.parallel_state")

    class GroupCoordinator:
        rank_in_group, world_size = 0, 1

        @property
        def is_first_rank(self):
            return True

        @property
        def is_last_rank(self):
            return True

    state.GroupCoordinator = GroupCoordinator
    state._PP = GroupCoordinator()
    modules["vllm.distributed"].parallel_state = state
    module("vllm.model_executor")
    module("vllm.model_executor.model_loader")
    loader = module("vllm.model_executor.model_loader.default_loader")
    loader.DefaultModelLoader = type("DefaultModelLoader", (), {
        "load_weights": lambda self, model, config: None})
    import contextlib

    torch = module("torch")
    torch.cuda = types.SimpleNamespace(
        mem_get_info=lambda index: (10 * 2 ** 30, 24 * 2 ** 30))
    torch.inference_mode = contextlib.nullcontext
    for name, made in modules.items():
        monkeypatch.setitem(sys.modules, name, made)

    from looma_stage import vllm_patch, vllm_worker

    monkeypatch.setattr(vllm_patch, "_applied", False)
    monkeypatch.setattr(vllm_worker, "_CLASS", None)
    monkeypatch.setattr("looma_stage.vllm_engine._hold_config", lambda _c: None)
    return modules


def _vllm_config(start=12, end=24, total=36, world=2):
    return types.SimpleNamespace(
        additional_config={"looma_stage": {
            "start_layer": start, "end_layer": end, "num_model_layers": total}},
        parallel_config=types.SimpleNamespace(world_size=world),
        cache_config=types.SimpleNamespace(gpu_memory_utilization=0.5))


def _worker(fake_vllm, rank=0, **stage):
    from looma_stage import vllm_worker

    made = vllm_worker.StageWorker(
        vllm_config=_vllm_config(**stage), local_rank=rank, rank=rank,
        distributed_init_method="tcp://127.0.0.1:1")
    made.init_device()
    made.load_model()
    return made


# --------------------------------------------------------------- подъём
def test_класс_собирается_один_раз(fake_vllm):
    from looma_stage import vllm_worker

    assert vllm_worker.StageWorker is vllm_worker.StageWorker
    assert vllm_worker.StageWorker.__qualname__ == "StageWorker"


def test_подмены_накладываются_в_каждом_воркере(fake_vllm):
    """Именно в воркере, а не в драйвере: у драйвера карты нет, и грузит
    веса не он. Заплата на загрузчик, срез слоёв, группа по слоям — всё
    должно стоять там, где идёт загрузка."""
    from looma_stage import vllm_patch

    worker = _worker(fake_vllm)
    state = fake_vllm["vllm.distributed.parallel_state"]
    assert vllm_patch._applied, "заплата на загрузчик не наложена"
    assert worker.seen_range == (12, 24), "срез не стоял во время загрузки"
    assert (state._PP.is_first_rank, state._PP.is_last_rank) == (False, False)
    # И после загрузки подмена снята — иначе испорчен весь процесс.
    utils = fake_vllm["vllm.distributed.utils"]
    assert utils.get_pp_indices(36, 0, 1) == (0, 36)


def test_исполнитель_строится_наш_и_один(fake_vllm):
    """Штатный init_device берёт класс исполнителя по имени в момент вызова;
    на это время имя указывает на наш. Второй исполнитель — это второй
    комплект буферов на карте ещё до загрузки весов."""
    worker = _worker(fake_vllm)
    assert type(worker.model_runner).__name__ == "StageRunner"
    assert len(_Runner.made) == 1
    runners = fake_vllm["vllm.v1.worker.gpu_model_runner"]
    assert runners.GPUModelRunner is _Runner, "имя в модуле vLLM не возвращено"


def test_чужой_исполнитель_заменяется_а_не_остаётся(fake_vllm, caplog):
    """Версия, где имя импортировано иначе, построит обычный — он не умеет
    ни принять входящие тензоры, ни отдать свои. Заменяем и говорим."""
    import logging

    original = _Worker.init_device

    def stubborn(self):
        self.trail.append("device")
        self.model_runner = _Runner(self.vllm_config, self.device)

    _Worker.init_device = stubborn
    try:
        with caplog.at_level(logging.WARNING, logger="looma_stage.vllm_worker"):
            worker = _worker(fake_vllm)
    finally:
        _Worker.init_device = original
    assert type(worker.model_runner).__name__ == "StageRunner"
    assert "заменяю" in caplog.text


def test_не_тот_срез_отказ_при_загрузке(fake_vllm):
    """Собрал не столько, сколько просили, — значит, подмена не сработала и
    воркер держит чужие слои."""
    with pytest.raises(RunnerRefused, match="просили 6 слоёв, а собралось 12"):
        _worker(fake_vllm, start=12, end=18)


def test_место_под_кэш_считается_от_свободного_на_своей_карте(fake_vllm):
    worker = _worker(fake_vllm)
    assert worker.stage_cache_room() == 5 * 2 ** 30
    assert worker.stage_layers_built() == 12


# ------------------------------------------------------------------ шаг
def test_входящие_кладутся_на_свою_карту(fake_vllm):
    worker = _worker(fake_vllm, rank=1)
    worker.model_runner.answer = _Intermediate({"hidden_states": _Tensor("h")})
    worker.stage_step("батч", {"hidden_states": _Tensor("h", "cpu")}, expected=1)
    _scheduled, tensors = worker.model_runner.executed[-1]
    assert isinstance(tensors, _Intermediate)
    assert tensors.tensors["hidden_states"].where == "cuda:1"


def test_первая_стадия_входящие_не_берёт(fake_vllm):
    worker = _worker(fake_vllm, start=0, end=12)
    worker.model_runner.answer = _Intermediate({"hidden_states": _Tensor("h")})
    worker.stage_step("батч", {"hidden_states": _Tensor("h")}, expected=1)
    assert worker.model_runner.executed[-1][1] is None


def test_отвечает_только_нулевой_и_с_процессора(fake_vllm):
    """У остальных результат тот же — возить его через очередь незачем. А
    CUDA-тензор через pickle — это IPC-дескриптор, который в чужом процессе
    без той же карты не откроется."""
    first = _worker(fake_vllm, rank=0)
    first.model_runner.answer = _Intermediate({"hidden_states": _Tensor("h", "cuda:0")})
    hidden, logits = first.stage_step("батч", None, expected=1)
    assert logits is None and hidden["hidden_states"].where == "cpu"

    second = _worker(fake_vllm, rank=1)
    second.model_runner.answer = _Intermediate({"hidden_states": _Tensor("h", "cuda:1")})
    assert second.stage_step("батч", None, expected=1) is None
    assert second.model_runner.executed, "считать обязан и ненулевой"


def test_последняя_стадия_отдаёт_логиты_копией(fake_vllm):
    worker = _worker(fake_vllm, start=24, end=36)
    worker.model_runner.answer = None
    worker.model_runner.execute_model_state = types.SimpleNamespace(
        logits=_Tensor("логиты", "cuda:0"))
    worker.model_runner.sample_tokens = lambda _grammar: setattr(
        worker.model_runner, "execute_model_state", None)
    hidden, logits = worker.stage_step("батч", {"hidden_states": _Tensor("h")},
                                       expected=1)
    assert hidden is None and logits.where == "cpu"
    assert worker.model_runner.execute_model_state is None, "шаг не закрыт"


def test_ненулевой_ранг_последней_стадии_тоже_закрывает_шаг(fake_vllm):
    """Коллективы NCCL ждут всех, и состояние шага у каждого своё: не закрой
    его на ранге 1 — следующий шаг упадёт именно там, а ответ читается с
    нулевого, и по нему причины не видно."""
    worker = _worker(fake_vllm, rank=1, start=24, end=36)
    worker.model_runner.answer = None
    worker.model_runner.execute_model_state = types.SimpleNamespace(logits=None)
    worker.model_runner.sample_tokens = lambda _grammar: setattr(
        worker.model_runner, "execute_model_state", None)
    assert worker.stage_step("батч", {"hidden_states": _Tensor("h")}, expected=1) is None
    assert worker.model_runner.execute_model_state is None
