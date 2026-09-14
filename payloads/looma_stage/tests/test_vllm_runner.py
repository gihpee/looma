"""Решения, от которых зависит, что вообще соберётся.

vLLM тут нет и не нужно: проверяется арифметика среза и то, что подменённая
глобальная функция возвращается на место. Ошибка в первом даёт стадию, которая
считает чужие слои и молча отвечает чушью; во втором — испорченный процесс,
где следующая попытка загрузки грузит не то.
"""

from __future__ import annotations

import sys
import types

import pytest

from looma_stage.vllm_runner import RunnerRefused, layer_range, stage_role


# ------------------------------------------------------------------ роль
def test_срез_с_начала_делает_стадию_первой():
    assert stage_role(0, 12, 36) == (True, False)


def test_срез_до_конца_делает_стадию_последней():
    assert stage_role(24, 36, 36) == (False, True)


def test_одна_стадия_и_первая_и_последняя():
    """Модель целиком на одном узле — обычный случай, а не вырожденный."""
    assert stage_role(0, 36, 36) == (True, True)


def test_середина_не_строит_ни_эмбеддингов_ни_головы():
    assert stage_role(12, 24, 36) == (False, False)


@pytest.mark.parametrize("start, end, total", [
    (0, 37, 36),      # за край модели
    (-1, 12, 36),     # отрицательное начало
    (12, 12, 36),     # пустой срез
    (24, 12, 36),     # вывернутый
    (0, 12, 0),       # модель без слоёв
])
def test_негодный_срез_отвергается(start, end, total):
    """Молча взять не тот срез — значит получить стадию, которая считает чужие
    слои и отвечает связной чушью, не падая нигде."""
    with pytest.raises(RunnerRefused):
        stage_role(start, end, total)


# --------------------------------------------------------------- подмена
@pytest.fixture
def vllm_utils(monkeypatch):
    """Внутренности vLLM, которых на этой машине нет."""
    utils = types.ModuleType("vllm.distributed.utils")
    utils.get_pp_indices = lambda num_layers, rank, world_size: (0, num_layers)
    for name in ("vllm", "vllm.distributed"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "vllm.distributed.utils", utils)
    return utils


def test_на_время_загрузки_слои_наши(vllm_utils):
    with layer_range(12, 24):
        assert vllm_utils.get_pp_indices(36, 0, 1) == (12, 24)
        # Аргументы не важны: сколько бы vLLM ни насчитал, строит он наш срез.
        assert vllm_utils.get_pp_indices(999, 7, 8) == (12, 24)


def test_после_загрузки_всё_как_было(vllm_utils):
    было = vllm_utils.get_pp_indices
    with layer_range(12, 24):
        pass
    assert vllm_utils.get_pp_indices is было


def test_после_ПАДЕНИЯ_загрузки_тоже_как_было(vllm_utils):
    """Подменённая функция глобальная. Оставить её после неудачи — испортить
    всё, что попробует грузить модель следом, включая сообщение об ошибке."""
    было = vllm_utils.get_pp_indices
    with pytest.raises(RuntimeError, match="веса не те"):
        with layer_range(12, 24):
            raise RuntimeError("веса не те")
    assert vllm_utils.get_pp_indices is было


def test_без_vllm_отказ_называет_причину(monkeypatch):
    for name in list(sys.modules):
        if name.startswith("vllm"):
            monkeypatch.delitem(sys.modules, name, raising=False)
    real = __import__

    def guarded(name, *args, **kwargs):
        if name.startswith("vllm"):
            raise ImportError(f"No module named {name!r}")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded)
    with pytest.raises(RunnerRefused, match="версию"):
        with layer_range(0, 12):
            pass


# ---------------------------------------------------------------- кэш
def test_рабочая_половина_кэша_обязательна():
    """Со стенда: кэш построен, модель загружена, а первый шаг падает на

        IndexError: list index out of range   (в attn_groups[0])

    Потому что рабочая половина — та, что выделяет тензоры и связывает их со
    слоями внимания, — не была вызвана вовсе. По сообщению об этом не
    догадаться: оно про пустой список, а не про пропущенный шаг.

    Теперь она заводится в воркерах штатным `initialize_from_config`, а
    планировщиковая половина — менеджер блоков — в драйвере, одна на все.
    """
    import inspect

    from looma_stage import vllm_runner

    source = inspect.getsource(vllm_runner.lay_out_cache)
    assert "initialize_from_config" in source, "рабочая половина кэша не заводится"
    # И планировщиковая тоже: без неё нечем выдавать блоки под батч.
    assert "KVCacheManager" in source


class _Executor:
    """Исполнитель vLLM, каким его видит раскладка кэша: отвечает на RPC."""

    def __init__(self, room):
        self.room = room
        self.calls = []

    def collective_rpc(self, method, args=(), kwargs=None, **_options):
        self.calls.append((method, args))
        if method == "get_kv_cache_spec":
            return ["спецификация"] * len(self.room)
        if method == "stage_cache_room":
            return list(self.room)
        return [None] * len(self.room)


@pytest.fixture
def vllm_cache(monkeypatch):
    """Внутренности кэша vLLM: запоминают, с каким местом их позвали."""
    asked = {}

    def get_kv_cache_configs(*, vllm_config, kv_cache_specs, available_memory):
        asked["memory"] = list(available_memory)
        return ["раскладка"] * len(kv_cache_specs)

    utils = types.ModuleType("vllm.v1.core.kv_cache_utils")
    utils.get_kv_cache_configs = get_kv_cache_configs
    utils.generate_scheduler_kv_cache_config = lambda configs: "для планировщика"
    manager = types.ModuleType("vllm.v1.core.kv_cache_manager")
    manager.KVCacheManager = lambda **kwargs: ("менеджер", kwargs)
    for name in ("vllm", "vllm.v1", "vllm.v1.core"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "vllm.v1.core.kv_cache_utils", utils)
    monkeypatch.setitem(sys.modules, "vllm.v1.core.kv_cache_manager", manager)
    return asked


def test_кэш_раскладывается_по_самой_занятой_карте(vllm_cache):
    """Раскладка обязана быть одной на всех картах: блоки под одну и ту же
    последовательность лежат на всех одинаково. Значит, задаёт её та карта,
    где свободно меньше всего, — иначе на ней блоков не хватит."""
    from looma_stage.vllm_runner import lay_out_cache

    executor = _Executor(room=[8 * 2 ** 30, 5 * 2 ** 30, 7 * 2 ** 30])
    config, (name, kwargs) = lay_out_cache(executor, "конфиг", block_size=16,
                                           max_model_len=4096)
    assert vllm_cache["memory"] == [5 * 2 ** 30] * 3
    assert config == "для планировщика" and name == "менеджер"
    assert kwargs["hash_block_size"] == 16 and kwargs["max_model_len"] == 4096


def test_рабочая_половина_заводится_на_всех_воркерах(vllm_cache):
    from looma_stage.vllm_runner import lay_out_cache

    executor = _Executor(room=[2 ** 30, 2 ** 30])
    lay_out_cache(executor, "конфиг", block_size=16, max_model_len=64)
    settled = [args for method, args in executor.calls
               if method == "initialize_from_config"]
    assert settled == [(["раскладка", "раскладка"],)]


def test_без_воркеров_раскладывать_нечего(vllm_cache):
    from looma_stage.vllm_runner import lay_out_cache

    with pytest.raises(RunnerRefused, match="ни один воркер"):
        lay_out_cache(_Executor(room=[]), "конфиг", block_size=16,
                      max_model_len=64)


def test_буфер_под_входящие_заводится_до_шага():
    """Со стенда: вторая стадия упала на

        assert self.intermediate_tensors is not None

    — утверждении, из которого не следует, что буфер кто-то должен был
    выделить. vLLM не принимает тензоры напрямую: он копирует их в свой
    буфер и нарезает по размеру батча.
    """
    import inspect

    from looma_stage import vllm_runner

    source = inspect.getsource(vllm_runner)
    assert "_ensure_incoming" in source
    assert "make_empty_intermediate_tensors" in source
    # И только у неголовной: первой входящие тензоры не приходят вовсе.
    assert "if not self.is_first_stage:" in source


# ------------------------------------------------------ группа конвейера
@pytest.fixture
def vllm_groups(monkeypatch):
    """Группа конвейера, какой её собрал vLLM: отвечает по номеру ранга."""
    state = types.ModuleType("vllm.distributed.parallel_state")

    class GroupCoordinator:
        def __init__(self, ranks):
            self.ranks = ranks
            self.rank_in_group = 0
            self.world_size = len(ranks)

        @property
        def is_first_rank(self):
            return self.rank_in_group == 0

        @property
        def is_last_rank(self):
            return self.rank_in_group == self.world_size - 1

    state.GroupCoordinator = GroupCoordinator
    state._PP = GroupCoordinator([3])
    distributed = types.ModuleType("vllm.distributed")
    distributed.parallel_state = state
    monkeypatch.setitem(sys.modules, "vllm", types.ModuleType("vllm"))
    monkeypatch.setitem(sys.modules, "vllm.distributed", distributed)
    monkeypatch.setitem(sys.modules, "vllm.distributed.parallel_state", state)
    return state


def test_группа_подменяется_на_месте_а_не_строится_заново(vllm_groups):
    """Строить вторую группу — значит звать коллективный `new_group`, и при
    tensor parallelism у каждого воркера свой список групп конвейера: такой
    вызов повис бы на первом узле с двумя картами. Подменяется класс того же
    объекта — коллективов у этого нет."""
    from looma_stage.vllm_runner import replace_pipeline_group

    before = vllm_groups._PP
    replace_pipeline_group(12, 24, 36)
    after = vllm_groups._PP
    assert after is before, "группа построена заново"
    assert (after.is_first_rank, after.is_last_rank) == (False, False)
    assert after.ranks == [3], "состояние группы потеряно"


def test_роль_по_слоям_а_не_по_рангу(vllm_groups):
    from looma_stage.vllm_runner import replace_pipeline_group

    replace_pipeline_group(24, 36, 36)
    assert (vllm_groups._PP.is_first_rank, vllm_groups._PP.is_last_rank) == (False, True)


def test_без_группы_отказ_называет_порядок(vllm_groups):
    from looma_stage.vllm_runner import replace_pipeline_group

    vllm_groups._PP = None
    with pytest.raises(RunnerRefused, match="порядок инициализации"):
        replace_pipeline_group(0, 12, 36)
