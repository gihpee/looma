

def test_auto_выбирает_ускоритель_этой_машины():
    """Без этого смешанный конвейер невозможен: устройство приходит одним
    флагом на всю модель, а Mac и машина с NVIDIA стоят в ней рядом. Любое
    жёсткое значение неверно для половины стадий."""
    import torch

    from looma_stage.loader import best_device, resolve_devices

    выбрано = best_device()
    assert выбрано in ("cuda", "mps", "cpu")
    # То же, что выбрал бы torch, если спросить его прямо.
    if torch.cuda.is_available():
        assert выбрано == "cuda"
    elif torch.backends.mps.is_available():
        assert выбрано == "mps"
    else:
        assert выбрано == "cpu"
    assert resolve_devices("auto") == resolve_devices(выбрано)


def test_явное_устройство_сильнее_авто():
    """Оператор, разделивший машину между двумя стадиями, называет карту
    поимённо — и его слово должно быть последним."""
    from looma_stage.loader import resolve_devices

    assert [str(d) for d in resolve_devices("cpu")] == ["cpu"]


def test_на_metal_редкие_операции_не_роняют_стадию(monkeypatch):
    """Metal покрывает не весь torch: у трансформеров попадается операция,
    которой в нём нет, и без запасного пути стадия падает посреди загрузки с
    сообщением про неподдерживаемый оператор."""
    import os

    from looma_stage.loader import _allow_mps_fallback

    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    _allow_mps_fallback()

    assert os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"


def test_выбор_оператора_не_перекрывается(monkeypatch):
    """Он мог выключить запасной путь нарочно — чтобы увидеть, на какой именно
    операции упирается модель, а не смотреть, как она тихо считает втрое
    дольше."""
    import os

    from looma_stage.loader import _allow_mps_fallback

    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "0")
    _allow_mps_fallback()

    assert os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] == "0"
