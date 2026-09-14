"""Сколько карты просит стадия.

vLLM выделяет KV-кэш заранее и на всю отведённую долю. Доля наугад означает,
что стадия крошечной модели занимает столько же, сколько огромной, и соседу на
чужом узле места не остаётся. Здесь — арифметика, которая считает это от того,
что стадия обещает обслужить.
"""

from __future__ import annotations

import types

import pytest

from looma_stage import vllm_engine
from looma_stage.vllm_engine import (
    OVERHEAD_BYTES,
    RunnerRefused,
    dtype_bytes,
    kv_bytes_per_token,
    plan_memory,
)

ГБ = 1024 ** 3


def config(**kwargs):
    base = {"num_key_value_heads": 8, "num_attention_heads": 32,
            "hidden_size": 2560, "head_dim": 128}
    return types.SimpleNamespace(**{**base, **kwargs})


# ------------------------------------------------------------ байты dtype
@pytest.mark.parametrize("name, size", [
    ("bfloat16", 2), ("BF16", 2), ("float32", 4), ("float8", 1),
])
def test_известные_типы(name, size):
    assert dtype_bytes(name) == size


def test_неизвестный_тип_отказ_а_не_догадка():
    """Ошибка вдвое даёт вдвое неверный кэш: узел либо не поднимется, либо
    пообещает вдвое больше, чем сможет."""
    with pytest.raises(RunnerRefused, match="сколько байт занимает"):
        dtype_bytes("float4")


# ------------------------------------------------------- цена одного токена
def test_считается_по_kv_головам_а_не_по_головам_внимания():
    """У моделей с GQA их в несколько раз меньше. Считать по головам внимания
    значило бы завысить кэш вчетверо и не поднять стадию там, где памяти
    хватало."""
    по_kv = kv_bytes_per_token(config(), layers=1, dtype="bfloat16")
    по_вниманию = kv_bytes_per_token(config(num_key_value_heads=32),
                                     layers=1, dtype="bfloat16")
    assert по_kv * 4 == по_вниманию
    assert по_kv == 2 * 8 * 128 * 2       # ключи+значения × головы × размер × байты


def test_цена_растёт_со_слоями_среза():
    один = kv_bytes_per_token(config(), layers=1, dtype="bfloat16")
    восемнадцать = kv_bytes_per_token(config(), layers=18, dtype="bfloat16")
    assert восемнадцать == один * 18


def test_размер_головы_выводится_когда_не_назван():
    выведено = kv_bytes_per_token(config(head_dim=None, hidden_size=4096,
                                         num_attention_heads=32),
                                  layers=1, dtype="bfloat16")
    assert выведено == 2 * 8 * 128 * 2


def test_конфиг_без_голов_отвергается():
    with pytest.raises(RunnerRefused, match="посчитать размер кэша нечем"):
        kv_bytes_per_token(config(num_key_value_heads=None,
                                  num_attention_heads=None),
                           layers=1, dtype="bfloat16")


# -------------------------------------------------------------- план памяти
def plan(**kwargs):
    base = dict(per_token=72 * 1024, weights_bytes=2 * ГБ, total_bytes=24 * ГБ,
                budget_bytes=24 * ГБ, max_sequences=8, max_model_len=4096)
    return plan_memory(**{**base, **kwargs})


def test_просим_столько_сколько_обещаем():
    """Не долю карты, а место под обещанную ёмкость."""
    сделано = plan(max_sequences=8)
    кэш = 8 * 4096 * 72 * 1024
    assert сделано.bytes_needed == 2 * ГБ + OVERHEAD_BYTES + кэш
    assert сделано.max_sequences == 8


def test_маленькая_модель_не_забирает_всю_карту():
    """Ради этого всё и затевалось: рядом живёт вторая стадия того же
    кластера."""
    сделано = plan(per_token=8 * 1024, weights_bytes=ГБ, max_sequences=4)
    assert сделано.utilisation < 0.5


def test_не_влезло_уменьшаем_обещание_а_не_кэш():
    """Иначе узел принимал бы запросы, которые не может досчитать: голова
    видит свой потолок, а блоки кончаются у стадии."""
    сделано = plan(max_sequences=64, total_bytes=24 * ГБ)
    assert сделано.max_sequences < 64
    assert "узел обещает меньше" in сделано.why


def test_доля_не_переходит_потолок():
    """Выше него vLLM не оставляет места под собственные буферы."""
    сделано = plan(max_sequences=1000)
    assert сделано.utilisation <= vllm_engine.MAX_UTILISATION


def test_квота_ограничивает_сильнее_карты():
    просторно = plan(budget_bytes=24 * ГБ, max_sequences=64)
    тесно = plan(budget_bytes=8 * ГБ, max_sequences=64)
    assert тесно.max_sequences < просторно.max_sequences


def test_если_не_влезает_даже_одна_отказ_с_числами():
    """Молча взять меньше — значит пообещать то, чего нет."""
    with pytest.raises(RunnerRefused, match="даже под одну последовательность"):
        plan(total_bytes=4 * ГБ, budget_bytes=4 * ГБ, weights_bytes=2 * ГБ)


def test_объяснение_называет_из_чего_сложилось():
    """Число, взявшееся ниоткуда, невозможно оспорить."""
    why = plan().why
    for кусок in ("кэша", "веса", "запас", "итого"):
        assert кусок in why


# ------------------------------------------------------------ по картам
def test_веса_делятся_на_все_карты():
    assert vllm_engine.per_card(8 * ГБ, 4) == 2 * ГБ


def test_одна_карта_берёт_всё():
    assert vllm_engine.per_card(8 * ГБ, 1) == 8 * ГБ
    assert vllm_engine.per_card(8 * ГБ, 0) == 8 * ГБ


def test_кэш_делится_по_головам_а_не_по_картам():
    """GQA-модель с 8 KV-головами на 16 картах: vLLM головы дублирует, и на
    карте лежит 1/8 кэша, а не 1/16. Считать 1/16 значило бы обещать кэш,
    которого на карте нет."""
    assert vllm_engine.per_card(16 * ГБ, 16, heads=8) == 2 * ГБ
    assert vllm_engine.per_card(16 * ГБ, 4, heads=8) == 4 * ГБ


def test_деление_округляется_вверх():
    """Недобрать байт на карту — это на большом батче не досчитать блок."""
    assert vllm_engine.per_card(10, 3) == 4


def test_план_на_узле_считается_от_карты(monkeypatch):
    """Квота узла — на все карты (`карт × меньшая карта`, как считает
    оркестратор); на карту приходится её доля, а веса и кэш — свои доли."""
    seen = {}
    monkeypatch.setattr(vllm_engine, "card_bytes", lambda cards: 24 * ГБ)
    monkeypatch.setattr(vllm_engine, "weights_size", lambda path: 8 * ГБ)
    monkeypatch.setattr(vllm_engine, "kv_bytes_per_token",
                        lambda config, *, layers, dtype: 64 * 1024)
    monkeypatch.setattr(vllm_engine, "plan_memory",
                        lambda **kwargs: seen.update(kwargs) or "план")
    config = types.SimpleNamespace(num_key_value_heads=8)
    monkeypatch.setitem(__import__("sys").modules, "transformers",
                        types.SimpleNamespace(AutoConfig=types.SimpleNamespace(
                            from_pretrained=lambda path: config)))

    assert vllm_engine.plan_for_shard("модель", layers=18, dtype="bfloat16",
                                      vram_quota_bytes=80 * ГБ, max_sequences=8,
                                      max_model_len=4096, cards=4) == "план"
    assert seen["weights_bytes"] == 2 * ГБ
    assert seen["per_token"] == 16 * 1024
    assert seen["budget_bytes"] == 20 * ГБ
    assert seen["total_bytes"] == 24 * ГБ


def test_память_карты_это_самая_маленькая(monkeypatch):
    """Доля vLLM одна на все карты — считать её надо от той, где меньше."""
    sizes = [48 * ГБ, 24 * ГБ, 48 * ГБ]
    torch = types.SimpleNamespace(cuda=types.SimpleNamespace(
        get_device_properties=lambda index: types.SimpleNamespace(
            total_memory=sizes[index])))
    monkeypatch.setitem(__import__("sys").modules, "torch", torch)
    assert vllm_engine.card_bytes(3) == 24 * ГБ
    assert vllm_engine.card_bytes(1) == 48 * ГБ


# -------------------------------------------------------------- /dev/shm
def test_очередям_нужно_место_на_каждый_воркер(monkeypatch):
    monkeypatch.delenv("VLLM_MQ_MAX_CHUNK_BYTES_MB", raising=False)
    one, four = vllm_engine.shm_needed(1), vllm_engine.shm_needed(4)
    assert four - one == 3 * 10 * 24 * 1024 ** 2


def test_тесный_shm_называется_вслух(monkeypatch, caplog):
    """Кончится он не при создании, а при первой записи за край — воркер
    умрёт по SIGBUS без единой строки. Пусть строка будет хотя бы здесь."""
    import logging

    monkeypatch.setattr(vllm_engine, "shm_room", lambda: 64 * 1024 ** 2)
    with caplog.at_level(logging.WARNING, logger="looma_stage.vllm_engine"):
        vllm_engine.warn_if_shm_tight(4)
    assert "--shm-size" in caplog.text and "SIGBUS" in caplog.text


def test_просторный_или_отсутствующий_shm_молчит(monkeypatch, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="looma_stage.vllm_engine"):
        monkeypatch.setattr(vllm_engine, "shm_room", lambda: 64 * 1024 ** 3)
        vllm_engine.warn_if_shm_tight(4)
        monkeypatch.setattr(vllm_engine, "shm_room", lambda: -1)
        vllm_engine.warn_if_shm_tight(4)
    assert caplog.text == ""
