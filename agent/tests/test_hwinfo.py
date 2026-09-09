

# --------------------------------------------------------------- диск узла
def test_диск_считается_по_тому_а_не_по_машине(tmp_path):
    """Кэши и каталоги задач лежат на одном томе, и вытеснение считает квоты
    именно от него."""
    from looma_agent.hwinfo import disk_bytes

    free, total = disk_bytes(tmp_path)
    assert 0 < free <= total


def test_свободное_место_без_резерва_под_root(tmp_path):
    """f_bavail, а не f_bfree: часть блоков задаче не отдадут, и обещать их
    хуже, чем показать меньше."""
    import os

    from looma_agent.hwinfo import disk_bytes

    stat = os.statvfs(tmp_path)
    free, _total = disk_bytes(tmp_path)
    assert free == stat.f_frsize * stat.f_bavail


def test_недоступный_путь_даёт_нули_а_не_падение():
    """Узел без одной цифры полезнее узла, который перестал отвечать."""
    from looma_agent.hwinfo import disk_bytes

    assert disk_bytes("/такого-пути-нет-и-не-будет") == (0, 0)


def test_на_маке_берутся_обычные_колёса_а_не_cpu():
    """Со стенда: узел на Apple ставил torch с индекса «cpu», потому что карты
    NVIDIA у него нет. Но на Apple Silicon именно обычные колёса с PyPI собраны
    с Metal, а на индексе «cpu» лежит сборка без него — то есть выбор отнимал у
    Mac единственный ускоритель."""
    import platform

    from looma_agent.tasks.env.python import _torch_tag

    tag = _torch_tag(["torch", "transformers"])
    if platform.system() == "Darwin":
        assert tag == "", "на Mac индекс подменять нельзя"
    else:
        assert tag in ("", "cpu") or tag.startswith("cu")


def test_без_torch_индекс_не_трогаем():
    """Требования без torch не должны уводить весь набор на чужой индекс:
    остальные пакеты живут на PyPI."""
    from looma_agent.tasks.env.python import _torch_tag

    assert _torch_tag(["transformers", "safetensors"]) == ""
