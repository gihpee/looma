"""Память по каждой карте — в телеметрии, а не только сумма.

Сумма непригодна для разреза модели по картам: она одинакова у одной карты
на 96 ГБ и у четырёх по 24. vLLM живёт на одной карте на процесс, и
многокарточная машина под ним становится столькими рангами, сколько карт, —
каждому нужна доля по ЕГО карте.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent import hwinfo


def test_без_nvml_список_берётся_из_nvidia_smi(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)      # import падает
    monkeypatch.setattr(hwinfo.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(hwinfo.subprocess, "check_output",
                        lambda *a, **kw: "23028\n11512\n")

    assert hwinfo.free_vram_per_gpu() == [23028 * 1024 * 1024, 11512 * 1024 * 1024]


def test_без_карт_список_пуст(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)
    monkeypatch.setattr(hwinfo.shutil, "which", lambda name: None)

    assert hwinfo.free_vram_per_gpu() == []


def test_список_уезжает_в_описании_железа(monkeypatch):
    from looma_agent import main as main_mod

    monkeypatch.setattr(main_mod, "free_vram_per_gpu", lambda: [1, 2, 3])
    message = main_mod.hardware_message()

    assert list(message.vram_free_per_gpu) == [1, 2, 3]
