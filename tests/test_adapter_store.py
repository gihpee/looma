"""Хранилище адаптеров: файлы на диске оркестратора, атомарно и без выхода
из каталога."""

import pytest

from looma.orchestrator.adapters import AdapterStore, adapter_root


def test_адаптер_кладётся_и_читается(tmp_path):
    store = AdapterStore(tmp_path / "adapters")
    assert not store.has("g1") and store.list() == []
    store.save("g1", {"adapter_config.json": b"{}", "adapter_model.safetensors": b"x"},
               meta={"label": "my-lora", "base_model": "Qwen/Qwen3-0.6B"})
    assert store.has("g1") and store.list() == ["g1"]
    assert store.files("g1")["adapter_model.safetensors"] == b"x"
    assert store.meta("g1")["label"] == "my-lora" and store.meta("g1")["bytes"] == 3
    # Повторное сохранение заменяет целиком.
    store.save("g1", {"adapter_config.json": b"{}", "adapter_model.safetensors": b"yy"},
               meta={})
    assert store.files("g1")["adapter_model.safetensors"] == b"yy"


def test_неполный_адаптер_и_плохое_имя_отвергаются(tmp_path):
    store = AdapterStore(tmp_path)
    with pytest.raises(ValueError, match="без"):
        store.save("g1", {"adapter_config.json": b"{}"}, meta={})
    for bad in ("", "..", "a/b"):
        with pytest.raises(ValueError):
            store.has(bad)
    with pytest.raises(FileNotFoundError):
        store.files("g2")


def test_корень_из_настроек_или_временный(tmp_path):
    class Config:
        data_dir = str(tmp_path)

    assert adapter_root(Config()) == tmp_path / "adapters"
    made = adapter_root(None)
    assert made.is_dir() and "looma-adapters" in made.name
