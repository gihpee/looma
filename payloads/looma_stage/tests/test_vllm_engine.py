"""Поднятие движка vLLM: порядок шагов и отказы.

Самого vLLM тут нет — он не ставится без карты. Проверяется то, что решает
исход: отказ до всякой работы, доля карты из квоты, и сверка «собралось ли
столько слоёв, сколько просили».
"""

from __future__ import annotations

import dataclasses
import sys
import types

import pytest

from looma_stage import vllm_engine
from looma_stage.vllm_runner import RunnerRefused


# --------------------------------------------------------------- отказы
def test_без_карты_отказ_называет_замену(monkeypatch):
    """vLLM без CUDA не работает, и выясняется это глубоко внутри —
    сообщением, по которому не видно, что дело в железе."""
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", torch)
    with pytest.raises(RunnerRefused, match="движок torch"):
        vllm_engine.require_cuda()


def test_с_картой_молчит(monkeypatch):
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: True)
    monkeypatch.setitem(sys.modules, "torch", torch)
    vllm_engine.require_cuda()


# ------------------------------------------------------- сколько собралось
class Layer:
    pass


class PPMissingLayer:
    """Заглушка vLLM на месте чужого слоя. Считать её нашей нельзя."""


def runner_with(layers):
    inner = types.SimpleNamespace(layers=layers)
    return types.SimpleNamespace(model=types.SimpleNamespace(model=inner))


def test_считаются_только_настоящие_слои():
    """vLLM ставит заглушки на месте слоёв чужих стадий. Посчитать их —
    решить, что срез собрался верно, когда он собрался целиком."""
    layers = [Layer(), Layer(), PPMissingLayer(), PPMissingLayer()]
    assert vllm_engine._count_layers(runner_with(layers)) == 2


def test_модель_без_слоёв_не_ломает_счёт():
    assert vllm_engine._count_layers(types.SimpleNamespace(model=None)) == 0


def _loading(monkeypatch, *, built, cards=2, order=None):
    """Всё, что подъём стадии зовёт снаружи, — без карты и без vLLM."""
    order = order if order is not None else []
    monkeypatch.setattr(vllm_engine, "require_cuda", lambda: None)
    monkeypatch.setattr(vllm_engine, "card_count", lambda: cards)
    monkeypatch.setattr(vllm_engine, "plan_for_shard",
                        lambda *a, **k: vllm_engine.Plan(
                            utilisation=0.5, max_sequences=8,
                            bytes_needed=0, why="в тесте"))
    monkeypatch.setattr(vllm_engine, "_build_config",
                        lambda *a, **k: _FakeConfig(k))
    # Конфиг vLLM держится открытым на всю жизнь процесса — тут его нет.
    monkeypatch.setattr(vllm_engine, "_hold_config",
                        lambda _c: order.append("конфиг"))
    monkeypatch.setattr(vllm_engine, "warn_if_shm_tight", lambda _c: None)
    executor = _FakeExecutor(built=built, cards=cards)
    monkeypatch.setattr(vllm_engine, "start_executor",
                        lambda _c: (order.append("воркеры"), executor)[1])
    monkeypatch.setattr(vllm_engine, "lay_out_cache",
                        lambda *a, **k: (order.append("кэш"), (None, None))[1])
    # Иначе тест пойдёт качать модель с HuggingFace.
    monkeypatch.setattr(vllm_engine, "prepare_weights",
                        lambda weights, **_k: weights)
    return executor, order


def test_несовпадение_числа_слоёв_отвергается(monkeypatch):
    """Подмена get_pp_indices — единственное, что удерживает vLLM от сборки
    всей модели. Её молчаливый провал даёт стадию, которая считает всё и ест
    всю карту, не сказав ни слова."""
    executor, _order = _loading(monkeypatch, built=36)
    with pytest.raises(RunnerRefused, match="просили 18 слоёв"):
        vllm_engine.load_shard("модель", start_layer=0, end_layer=18,
                               num_model_layers=36)
    # Воркеры — процессы с картами; после отказа их некому остановить,
    # кроме нас.
    assert executor.stopped


def test_воркеры_разошлись_в_числе_слоёв_отказ(monkeypatch):
    executor, _order = _loading(monkeypatch, built=[18, 36])
    with pytest.raises(RunnerRefused, match="разное число слоёв"):
        vllm_engine.load_shard("модель", start_layer=0, end_layer=18,
                               num_model_layers=36)
    assert executor.stopped


def test_конфиг_ставится_раньше_воркеров(monkeypatch):
    """Свежий vLLM спрашивает конфиг уже внутри `initialize_model_parallel`.

    Поставь его позже — падает на assert'е, в котором про конвейер нет ни
    слова: «Current vLLM config is not set... or a CustomOp was instantiated at
    module import time». Порядок этих шагов и есть весь смысл теста: конфиг,
    потом воркеры (в них и группа, и загрузка), потом кэш — когда веса уже
    на местах и видно, сколько осталось.
    """
    executor, order = _loading(monkeypatch, built=18)
    shard = vllm_engine.load_shard("модель", start_layer=0, end_layer=18,
                                   num_model_layers=36)
    assert order == ["конфиг", "воркеры", "кэш"]
    assert shard.cards == 2 and shard.as_dict()["карт"] == 2
    assert not executor.stopped


def test_срез_уезжает_воркерам_в_конфиге(monkeypatch):
    """Конфиг — единственное, что vLLM передаёт в процесс воркера при его
    создании; без среза в нём воркер соберёт всю модель."""
    seen = {}
    _loading(monkeypatch, built=18, cards=4)
    monkeypatch.setattr(vllm_engine, "_build_config",
                        lambda *a, **k: (seen.update(k), _FakeConfig(k))[1])
    vllm_engine.load_shard("модель", start_layer=18, end_layer=36,
                           num_model_layers=36)
    assert seen["cards"] == 4
    assert seen["stage"] == {"start_layer": 18, "end_layer": 36,
                             "num_model_layers": 36}


def test_закрытие_среза_останавливает_воркеры(monkeypatch):
    executor, _order = _loading(monkeypatch, built=18)
    shard = vllm_engine.load_shard("модель", start_layer=0, end_layer=18,
                                   num_model_layers=36)
    shard.close()
    assert executor.stopped


def test_негодный_срез_отвергается_до_загрузки(monkeypatch):
    monkeypatch.setattr(vllm_engine, "require_cuda", lambda: None)
    with pytest.raises(RunnerRefused, match="не помещается"):
        vllm_engine.load_shard("модель", start_layer=30, end_layer=40,
                               num_model_layers=36)


class _FakeConfig:
    def __init__(self, options=None):
        self.options = options or {}


class _FakeExecutor:
    """Исполнитель vLLM: воркеры отвечают на RPC, остановка запоминается."""

    def __init__(self, *, built, cards):
        self.built = built if isinstance(built, list) else [built] * cards
        self.stopped = False
        self.calls = []

    def collective_rpc(self, method, args=(), kwargs=None, **options):
        self.calls.append((method, args, kwargs or {}, options))
        if method == "stage_layers_built":
            return list(self.built)
        if method == "stage_step":
            return options.get("reply")
        return [None] * len(self.built)

    def shutdown(self):
        self.stopped = True


# ---------------------------------------------------------------- уборка
def test_уборка_разбирает_что_подняла(monkeypatch):
    разобрано = []
    state = types.ModuleType("vllm.distributed.parallel_state")
    state.destroy_model_parallel = lambda: разобрано.append("модель")
    state.destroy_distributed_environment = lambda: разобрано.append("окружение")
    distributed = types.ModuleType("vllm.distributed")
    distributed.parallel_state = state
    monkeypatch.setitem(sys.modules, "vllm", types.ModuleType("vllm"))
    monkeypatch.setitem(sys.modules, "vllm.distributed", distributed)
    monkeypatch.setitem(sys.modules, "vllm.distributed.parallel_state", state)

    torch = types.ModuleType("torch")
    torch.distributed = types.SimpleNamespace(
        is_initialized=lambda: True,
        destroy_process_group=lambda: разобрано.append("torch"))
    monkeypatch.setitem(sys.modules, "torch", torch)

    vllm_engine.shutdown()
    assert разобрано == ["модель", "окружение", "torch"]


def test_уборка_не_падает_когда_разбирать_нечего(monkeypatch):
    """Падать на уборке — худшее, что можно сделать с процессом, который и
    так уходит: настоящая причина ухода потеряется."""
    for name in list(sys.modules):
        if name.startswith("vllm"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    real = __import__

    def guarded(name, *args, **kwargs):
        if name.startswith("vllm"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded)
    vllm_engine.shutdown()      # молча


# ------------------------------------------------------------------- шаг
class Intermediate:
    """Стенд-ин для vllm.sequence.IntermediateTensors."""

    def __init__(self, tensors=None):
        self.tensors = dict(tensors or {})


@pytest.fixture
def sequence_module(monkeypatch):
    module = types.ModuleType("vllm.sequence")
    module.IntermediateTensors = Intermediate
    monkeypatch.setitem(sys.modules, "vllm", types.ModuleType("vllm"))
    monkeypatch.setitem(sys.modules, "vllm.sequence", module)
    return module


def test_тензоры_найдутся_как_бы_версия_их_ни_отдала(sequence_module):
    """Версии отличаются: одна возвращает их прямо, другая кладёт в состояние
    исполнителя. Обе должны пройти."""
    прямо = Intermediate()
    assert vllm_engine._hidden_from(types.SimpleNamespace(), прямо) is прямо

    в_состоянии = Intermediate()
    runner = types.SimpleNamespace(
        execute_model_state=types.SimpleNamespace(intermediate_tensors=в_состоянии))
    assert vllm_engine._hidden_from(runner, object()) is в_состоянии


def test_если_тензоров_нет_нигде_отказ_называет_что_пришло(sequence_module):
    """Гадать нельзя: молча вернуть None значит отправить дальше по конвейеру
    пустоту, и разбираться в этом будут на последней стадии."""
    with pytest.raises(RunnerRefused, match="вернул dict"):
        vllm_engine._hidden_from(types.SimpleNamespace(), {})


def test_неголовной_стадии_без_тензоров_считать_нечего(monkeypatch, sequence_module):
    """Иначе она посчитает мусор из неинициализированного буфера и отдаст его
    дальше — молча."""
    shard = vllm_engine.LoadedShard(
        start_layer=18, end_layer=36, num_layers=36, is_first=False,
        is_last=True, dtype="bfloat16", runner=types.SimpleNamespace())
    monkeypatch.setattr("looma_stage.vllm_batch.prefill", lambda *a, **k: "батч")
    monkeypatch.setattr("looma_stage.vllm_batch.decode", lambda *a, **k: "батч")

    with pytest.raises(RunnerRefused, match="тензоры от предыдущей не пришли"):
        vllm_engine.step(shard, [object()], incoming=None, first_step=True)


# ------------------------------------------------- тензоры через файл
def test_тензоры_переживают_дорогу_через_файл(tmp_path, monkeypatch):
    """Складываются они нашим форматом провода — тем самым, которым поедут
    между машинами. Проверить его тут ничего не стоит, а разойдись он с
    ожиданием — стадия получит мусор и посчитает его молча."""
    import torch

    from looma_stage import vllm_engine as engine

    tensors = {
        "hidden_states": torch.randn(3, 8, dtype=torch.bfloat16),
        "residual": torch.randn(3, 8, dtype=torch.bfloat16),
    }
    path = str(tmp_path / "hidden.bin")
    engine._save_hidden(tensors, path)

    restored = {}
    import json

    from looma_stage import wire

    layout = json.loads((tmp_path / "hidden.bin.json").read_text())
    blob = engine.pathlib_read(path)
    for name, where in layout.items():
        piece = blob[where["at"]:where["at"] + where["size"]]
        restored[name] = wire.from_wire(torch, piece, where["shape"], where["dtype"])

    assert sorted(restored) == ["hidden_states", "residual"]
    for name, original in tensors.items():
        assert torch.equal(restored[name], original), name
        assert restored[name].dtype == original.dtype, "dtype не должен расширяться"


# ---------------------------------------------------- текущий конфиг vLLM
def test_конфиг_держится_открытым(monkeypatch):
    """Со стенда: загрузка прошла, а раскладка кэша упала на

        AssertionError: Current vLLM config is not set

    из бэкенда внимания — места, которое к конфигу отношения не имеет. Части
    движка спрашивают «текущий конфиг» сами, без аргументов, и вне контекста
    это падает где угодно.
    """
    import contextlib

    открыт = []

    @contextlib.contextmanager
    def set_current(config):
        открыт.append(config)
        try:
            yield
        finally:
            открыт.remove(config)

    module = types.ModuleType("vllm.config")
    module.set_current_vllm_config = set_current
    monkeypatch.setitem(sys.modules, "vllm", types.ModuleType("vllm"))
    monkeypatch.setitem(sys.modules, "vllm.config", module)
    monkeypatch.setattr(vllm_engine, "_CONFIG", None)

    vllm_engine._hold_config("конфиг")
    assert открыт == ["конфиг"], "контекст не установлен"

    # Второй вызов ничего не меняет: стадия в процессе одна.
    vllm_engine._hold_config("другой")
    assert открыт == ["конфиг"]

    vllm_engine.shutdown()
    assert открыт == [], "контекст не закрылся на уборке"


def test_уборка_без_конфига_молчит(monkeypatch):
    monkeypatch.setattr(vllm_engine, "_CONFIG", None)
    for name in list(sys.modules):
        if name.startswith("vllm"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    real = __import__

    def guarded(name, *args, **kwargs):
        if name.startswith("vllm"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded)
    vllm_engine.shutdown()


# ------------------------------------------------- поля конфигов и версии
def test_неизвестное_поле_отбрасывается_и_называется(caplog):
    """Поля конфигов vLLM переезжают между версиями, и лишний аргумент роняет
    всё поднятие — сообщением про имя, а не про то, что версия другая."""
    import dataclasses
    import logging

    @dataclasses.dataclass
    class Старый:
        model: str = ""

    with caplog.at_level(logging.WARNING, logger="looma_stage.vllm_engine"):
        made = vllm_engine._config_with(Старый, model="м", enforce_eager=True)
    assert made.model == "м"
    assert any("enforce_eager" in r.getMessage() for r in caplog.records), (
        "молча потерянный enforce_eager вернёт захват графов и падение в нём")


def test_известные_поля_доходят():
    import dataclasses

    @dataclasses.dataclass
    class Новый:
        model: str = ""
        enforce_eager: bool = False

    made = vllm_engine._config_with(Новый, model="м", enforce_eager=True)
    assert made.enforce_eager is True


def test_поля_initvar_доходят_до_конструктора():
    """Со стенда: у SchedulerConfig `is_encoder_decoder` и `max_model_len` —
    InitVar. Конструктор их требует, `dataclasses.fields()` не показывает;
    отброшенные как неизвестные, они уронили подъём на «Field required»."""
    # `dataclasses` нужен в глобалах модуля: с отложенными аннотациями
    # InitVar распознаётся по имени модуля, а локальный импорт не виден.
    @dataclasses.dataclass
    class Планировщик:
        max_num_seqs: int = 0
        max_model_len: dataclasses.InitVar[int] = 0
        is_encoder_decoder: dataclasses.InitVar[bool] = False

        def __post_init__(self, max_model_len, is_encoder_decoder):
            self.seen = (max_model_len, is_encoder_decoder)

    made = vllm_engine._config_with(Планировщик, max_num_seqs=4, max_model_len=4096,
                                    is_encoder_decoder=False, async_scheduling=False)
    assert made.seen == (4096, False) and made.max_num_seqs == 4


def test_не_датакласс_собирается_как_есть():
    class Обычный:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    assert vllm_engine._config_with(Обычный, a=1).kwargs == {"a": 1}


# ------------------------------------------------------ урезанный чекпоинт
def test_стадии_дают_урезанный_чекпоинт(monkeypatch):
    """vLLM перечисляет каждый файл из индекса и открывает его: недостающий —
    ошибка, а не экономия. Поэтому рядом собирается вид из симлинков с
    переписанным индексом."""
    просили = {}

    def resolve(weights, shard=None, **_kwargs):
        просили["скачано для"] = (shard.start_layer, shard.end_layer)
        просили["роли"] = (shard.is_first, shard.is_last)
        return "/локально"

    def view(path, shard):
        просили["вид из"] = path
        return "/локально/вид"

    monkeypatch.setattr("looma_stage.loader.resolve_model_path", resolve)
    monkeypatch.setattr("looma_stage.loader.build_stage_checkpoint_view", view)

    got = vllm_engine.prepare_weights("Qwen/Qwen3-4B", start_layer=0, end_layer=18,
                                      is_first=True, is_last=False, dtype="bfloat16")
    assert got == "/локально/вид"
    assert просили["скачано для"] == (0, 18), "скачали не свой срез"
    assert просили["роли"] == (True, False)
    assert просили["вид из"] == "/локально"


def test_если_урезать_нечем_читаем_целиком(monkeypatch):
    """Единственный файл или незнакомые имена ключей — не отказ: стадия просто
    прочитает больше, чем ей нужно."""
    monkeypatch.setattr("looma_stage.loader.resolve_model_path",
                        lambda *a, **k: "/локально")
    monkeypatch.setattr("looma_stage.loader.build_stage_checkpoint_view",
                        lambda path, shard: path)

    got = vllm_engine.prepare_weights("модель", start_layer=0, end_layer=18,
                                      is_first=True, is_last=False, dtype="bfloat16")
    assert got == "/локально"


# ------------------------------------------------------- батч из нескольких
def test_шаг_без_последовательностей_отвергается():
    from looma_stage import vllm_engine

    with pytest.raises(vllm_engine.RunnerRefused, match="без единой"):
        vllm_engine.step(object(), [], first_step=True)


@pytest.mark.parametrize("text, expected", [
    ("1,2,3", [[1, 2, 3]]),
    ("1,2,3;4,5", [[1, 2, 3], [4, 5]]),
    ("1,2;;3", [[1, 2], [3]]),
    (" 1 , 2 ; 3 ", [[1, 2], [3]]),
])
def test_разбор_нескольких_промптов(text, expected):
    from looma_stage import vllm_engine

    assert vllm_engine._parse_prompts(text) == expected


def test_промпты_без_токенов_отвергаются():
    """Батч из пустой последовательности vLLM примет и посчитает ни за чем."""
    from looma_stage import vllm_engine

    with pytest.raises(vllm_engine.RunnerRefused, match="ни одного токена"):
        vllm_engine._parse_prompts(";;")


# --------------------------------------------------- двухфазный шаг vLLM
class _Sampled:
    """ModelRunnerOutput: токены, которые выбрал сэмплер vLLM."""

    def __init__(self, tokens, ids=None):
        self.sampled_token_ids = [list(t) if isinstance(t, (list, tuple)) else [t]
                                  for t in tokens]
        self.req_ids = list(ids or [f"r{i}" for i in range(len(tokens))])


class _TwoPhaseRunner:
    """Исполнитель vLLM 0.14: шаг разделён надвое, токены отдаёт вторая
    половина."""

    def __init__(self, tokens=(7,), ids=None):
        self.execute_model_state = types.SimpleNamespace(logits="логиты")
        self.sampled = 0
        self._tokens, self._ids = tokens, ids

    def sample_tokens(self, _grammar):
        if self.execute_model_state is None:
            raise AssertionError("позвали, когда закрывать было нечего")
        self.execute_model_state = None
        self.sampled += 1
        return _Sampled(self._tokens, self._ids)


def test_шаг_закрывается_и_следующий_проходит(sequence_module):
    """Не закрыть шаг — значит уронить СЛЕДУЮЩИЙ на «State error», уже после
    того, как первый токен уехал клиенту. Одиночной проверкой не ловится."""
    runner = _TwoPhaseRunner()
    vllm_engine.collect(runner, None, is_last=True, expected=1)
    assert runner.execute_model_state is None and runner.sampled == 1


def test_токен_берётся_у_сэмплера_vllm_а_не_выбирается_заново(sequence_module):
    """Со стенда: при температуре 1 у Qwen3 выпадали слоги, gpt-oss шёл
    кашей, при 0 — всё чисто. Исполнитель vLLM на последнем ранге продолжает
    с токена, который выбрал ЕГО сэмплер, а присланный игнорирует; выбирая
    свой поверх его логитов, мы показывали клиенту не тот токен, от которого
    модель продолжила. При argmax это совпадало — потому и не всплывало."""
    runner = _TwoPhaseRunner(tokens=(11, 22), ids=["a", "b"])
    _hidden, chosen = vllm_engine.collect(runner, None, is_last=True, expected=2)
    assert chosen == {"a": 11, "b": 22}


def test_старая_версия_отдаёт_токены_одним_вызовом(sequence_module):
    """До 0.14 `execute_model` возвращал всё сразу — тогда `sample_tokens`
    звать нечего, токены уже в ответе."""
    runner = types.SimpleNamespace()               # без sample_tokens
    _hidden, chosen = vllm_engine.collect(runner, _Sampled([5], ["x"]),
                                          is_last=True, expected=1)
    assert chosen == {"x": 5}


def test_асинхронный_вывод_дожидается_а_не_читает_заглушки(sequence_module):
    """С async scheduling `sample_tokens` отдаёт объект с `get_output`, а
    в самом ответе вместо токенов -1. Мы его выключили; но если версия
    включила — дожидаемся."""
    class Deferred:
        def get_output(self):
            return _Sampled([9], ["x"])

    runner = types.SimpleNamespace(
        execute_model_state=object(), sample_tokens=lambda _g: Deferred())
    _hidden, chosen = vllm_engine.collect(runner, None, is_last=True, expected=1)
    assert chosen == {"x": 9}


def test_состав_разошёлся_отказ_а_не_чужой_токен(sequence_module):
    """Токен не подписан ничем, кроме request_id; лишний или недостающий —
    значит порядок батча уже не тот, и молча продолжать нельзя."""
    with pytest.raises(RunnerRefused, match="состав батча разошёлся"):
        vllm_engine.collect(_TwoPhaseRunner(tokens=(1, 2)), None, is_last=True,
                            expected=1)
    with pytest.raises(RunnerRefused, match="не выбрал токен"):
        vllm_engine.collect(_TwoPhaseRunner(tokens=([],)), None, is_last=True,
                            expected=1)


def test_без_токенов_отказ_называет_что_пришло(sequence_module):
    runner = types.SimpleNamespace(execute_model_state=None)     # закрывать нечего
    with pytest.raises(RunnerRefused, match="вернул int"):
        vllm_engine.collect(runner, 7, is_last=True, expected=1)


def test_средняя_стадия_отдаёт_тензоры_а_не_токены(sequence_module):
    runner = _TwoPhaseRunner()
    answer = Intermediate()
    hidden, chosen = vllm_engine.collect(runner, answer, is_last=False, expected=1)
    assert hidden is answer and chosen is None
    assert runner.sampled == 0, "у средней стадии закрывать нечего"


# ------------------------------------------------------------ драйвер
class _Tensor:
    def __init__(self, name, where="cuda:0"):
        self.name, self.where = name, where

    def cpu(self):
        return _Tensor(self.name, "cpu")


def _driver(reply, cards=2):
    executor = _FakeExecutor(built=18, cards=cards)
    executor.collective_rpc = lambda method, args=(), kwargs=None, **o: (
        executor.calls.append((method, args, kwargs or {}, o)) or reply)
    return vllm_engine.StageDriver(executor, cards=cards, is_first=False,
                                   is_last=False), executor


def test_драйвер_шлёт_батч_всем_и_читает_нулевой(sequence_module):
    """Входящие тензоры — каждому воркеру целиком: при tensor parallelism
    вход слоя один на всех картах. Ответ — только с нулевого: у остальных
    он тот же."""
    driver, executor = _driver(reply=({"hidden_states": _Tensor("h", "cpu")}, None))
    incoming = {"hidden_states": _Tensor("h"), "residual": _Tensor("r")}
    hidden, logits = driver.run("батч", incoming, expected=3)

    method, args, kwargs, options = executor.calls[-1]
    assert method == "stage_step" and args[0] == "батч"
    assert {name: t.where for name, t in args[1].items()} == {
        "hidden_states": "cpu", "residual": "cpu"}, "на воркеры едет с процессора"
    assert kwargs == {"expected": 3}
    assert options["unique_reply_rank"] == 0
    assert options["timeout"] == vllm_engine.STEP_TIMEOUT_S, "шаг не ждёт вечно"
    assert isinstance(hidden, Intermediate) and logits is None


def test_драйвер_отдаёт_выбор_последней_стадии(sequence_module):
    driver, _executor = _driver(reply=(None, {"a": 3}))
    assert driver.run("батч", None, expected=1) == (None, {"a": 3})


def test_молчание_нулевого_воркера_отказ(sequence_module):
    driver, _executor = _driver(reply=None)
    with pytest.raises(RunnerRefused, match="нулевой воркер"):
        driver.run("батч", None, expected=1)


def test_шаг_собирает_батч_в_драйвере_и_шлёт_воркерам(monkeypatch, sequence_module):
    """Блоки под батч выдаются один раз, здесь; воркеры получают готовый
    состав в одном и том же порядке."""
    from looma_stage.scheduler import Sequence

    driver, executor = _driver(reply=(None, {"a": 3}))
    formed = []
    monkeypatch.setattr("looma_stage.vllm_batch.prefill",
                        lambda batch, runner: formed.append(("prefill", runner)) or "батч")
    monkeypatch.setattr("looma_stage.vllm_batch.decode",
                        lambda batch, runner: formed.append(("decode", runner)) or "батч")
    shard = vllm_engine.LoadedShard(
        start_layer=18, end_layer=36, num_layers=36, is_first=False,
        is_last=True, dtype="bfloat16", runner=driver)

    assert vllm_engine.step(shard, [Sequence("a", [1, 2])], incoming={},
                            first_step=True) == (None, {"a": 3})
    assert formed == [("prefill", driver)]
    assert executor.calls[-1][1][0] == "батч"


def test_старая_версия_закрывать_нечего():
    """До 0.14 всё отдавалось одним вызовом."""
    assert vllm_engine._finish_step(types.SimpleNamespace()) is None   # нет sample_tokens
    assert vllm_engine._finish_step(types.SimpleNamespace(
        sample_tokens=None, execute_model_state=None)) is None


# ------------------------------------------------------ компилятор для Triton
def test_компилятор_из_ziglang_когда_своего_нет(monkeypatch, tmp_path):
    """Со стенда: gpt-oss-20b (MoE + attention sinks) упал на первом шаге с
    «Failed to find C compiler» — Triton зван напрямую, запрет torch.compile
    тут не при чём. Компилятор едет pip-пакетом, обёртка пишется задачей."""
    import sys
    import types

    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setitem(sys.modules, "ziglang", types.ModuleType("ziglang"))
    monkeypatch.setenv("LOOMA_TASK_TMP", str(tmp_path))

    path = vllm_engine.provide_compiler()

    import os
    assert path == str(tmp_path / vllm_engine.COMPILER_WRAPPER)
    assert os.environ["CC"] == path, "воркеры берут CC из окружения"
    assert os.access(path, os.X_OK)
    text = open(path).read()
    assert text.startswith("#!/bin/sh") and "-m ziglang cc" in text
    assert sys.executable in text, "zig зовётся тем же интерпретатором, где он стоит"


def test_свой_компилятор_не_перекрывается(monkeypatch):
    monkeypatch.setenv("CC", "/usr/bin/cc")
    assert vllm_engine.provide_compiler() == "/usr/bin/cc"
    monkeypatch.delenv("CC")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/gcc" if name == "gcc" else None)
    assert vllm_engine.provide_compiler() == "/usr/bin/gcc"


def test_без_ziglang_предупреждение_а_не_падение(monkeypatch, caplog):
    """Плотные модели без компилятора работают — отказывать нельзя. Но пусть
    в логе будет, почему MoE упадёт."""
    import logging
    import sys

    monkeypatch.delenv("CC", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setitem(sys.modules, "ziglang", None)    # ImportError при импорте
    with caplog.at_level(logging.WARNING, logger="looma_stage.vllm_engine"):
        assert vllm_engine.provide_compiler() == ""
    assert "ziglang" in caplog.text and "MoE" in caplog.text


def test_компилятор_даётся_до_воркеров(monkeypatch):
    order = []
    monkeypatch.setattr(vllm_engine, "provide_compiler",
                        lambda: order.append("компилятор") or "")
    _loading(monkeypatch, built=18, order=order)
    vllm_engine.load_shard("модель", start_layer=0, end_layer=18,
                           num_model_layers=36)
    assert order.index("компилятор") < order.index("воркеры")


# ---------------------------------------------------------- раскладка выбора
def test_выбор_раскладывается_по_порядку_батча(sequence_module):
    from looma_stage.scheduler import Sequence

    engine = vllm_engine.VllmEngine.__new__(vllm_engine.VllmEngine)
    tokens = engine.sample_batch({"b": 2, "a": 1}, [Sequence("a", [0]), Sequence("b", [0])])
    assert tokens == [1, 2]


def test_нет_токена_для_кого_то_из_батча_отказ(sequence_module):
    """Подставить чужой — уехать ответом не тому клиенту."""
    from looma_stage.scheduler import Sequence

    engine = vllm_engine.VllmEngine.__new__(vllm_engine.VllmEngine)
    with pytest.raises(RunnerRefused, match="не выбрал токен для b"):
        engine.sample_batch({"a": 1}, [Sequence("a", [0]), Sequence("b", [0])])
