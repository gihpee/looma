"""Песочница macOS проверяется запуском, а не чтением профиля.

Политика доступа — тот случай, где «выглядит правильно» и «работает» расходятся
молча: неверный порядок правил не ломает запуск, он просто оставляет открытым
то, что должно быть закрыто. Поэтому здесь настоящий процесс и настоящие
отказы.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.tasks import sandbox

# Свой интерпретатор, а не sys.executable: рабочее окружение разработчика
# лежит в /Users, куда задаче хода нет. Задача запускается интерпретатором из
# кэша окружений — что и проверяется отдельно ниже.
SYSTEM_PYTHON = "/usr/bin/python3"

pytestmark = pytest.mark.skipif(
    not sandbox.available(), reason="песочница есть только на macOS")

# Что пробуем сделать изнутри. Печатает по строке на попытку, чтобы отказ был
# виден отдельно от падения самого процесса.
PROBE = """
import sys
def t(name, fn):
    try:
        fn()
        print(name + ": yes")
    except Exception:
        print(name + ": no")

root, task, envs, models = sys.argv[1:5]
t("secret",     lambda: open(root + "/secret.key").read())
t("neighbour",  lambda: open(root + "/tasks/other/work/data").read())
t("env-read",   lambda: open(envs + "/e1/lib.txt").read())
t("env-write",  lambda: open(envs + "/e1/sneak", "w").write("x"))
t("model-write", lambda: open(models + "/weights.bin", "w").write("x"))
t("own-write",  lambda: open(task + "/work/out", "w").write("x"))
t("system-write", lambda: open("/usr/local/looma-probe", "w").write("x"))
"""


@pytest.fixture
def node(tmp_path):
    """Узел в миниатюре: корень агента, кэши и две задачи."""
    root = tmp_path / "looma"
    task = root / "tasks" / "mine"
    (task / "work").mkdir(parents=True)
    (root / "tasks" / "other" / "work").mkdir(parents=True)
    (root / "tasks" / "other" / "work" / "data").write_text("чужое")
    (root / "envs" / "e1").mkdir(parents=True)
    (root / "envs" / "e1" / "lib.txt").write_text("библиотека")
    (root / "models").mkdir()
    (root / "secret.key").write_text("ключ узла")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return root, task, scratch


def run_inside(node) -> dict:
    root, task, scratch = node
    profile = sandbox.prepare(task_dir=task, scratch=scratch,
                              envs_dir=root / "envs", models_dir=root / "models",
                              agent_root=root)
    assert profile is not None, "профиль не записался"
    script = task / "work" / "probe.py"
    script.write_text(PROBE)
    argv = sandbox.wrap(
        [SYSTEM_PYTHON, str(script), str(root), str(task),
         str(root / "envs"), str(root / "models")],
        profile_path=profile, task_dir=task, scratch=scratch,
        envs_dir=root / "envs", models_dir=root / "models", agent_root=root)
    done = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    return dict(line.split(": ") for line in done.stdout.strip().splitlines())


def test_ключ_узла_и_соседи_недоступны(node):
    """Ровно то, ради чего на Linux заводится отдельный пользователь внутри
    контейнера. Задача — чужой код; ключ узла в её руках означает, что этим
    узлом можно распоряжаться от имени владельца."""
    got = run_inside(node)

    assert got["secret"] == "no"
    assert got["neighbour"] == "no"


def test_выданное_задаче_работает(node):
    """Изоляция, при которой задача не может писать себе в каталог, — это не
    изоляция, а неработающий узел."""
    got = run_inside(node)

    assert got["own-write"] == "yes"
    assert got["env-read"] == "yes", "без кэша окружений задача не запустится"
    # Веса качает та задача, которой они понадобились первой, и кладёт в общий
    # кэш узла.
    assert got["model-write"] == "yes"


def test_общее_нельзя_подменить(node):
    """Кэш окружений переиспользуют следующие задачи. Запись в него — это
    возможность подложить библиотеку тому, кто придёт после."""
    got = run_inside(node)

    assert got["env-write"] == "no"
    assert got["system-write"] == "no"


def test_без_песочницы_всё_это_открыто(node):
    """Иначе предыдущие три теста доказывали бы только то, что файлов нет."""
    root, task, scratch = node
    script = task / "work" / "probe.py"
    script.write_text(PROBE)
    done = subprocess.run(
        [SYSTEM_PYTHON, str(script), str(root), str(task),
         str(root / "envs"), str(root / "models")],
        capture_output=True, text=True, timeout=120)
    got = dict(line.split(": ") for line in done.stdout.strip().splitlines())

    assert got["secret"] == "yes"
    assert got["neighbour"] == "yes"
    assert got["env-write"] == "yes"


def test_интерпретатор_из_кэша_окружений_запускается(node):
    """Иначе изоляция была бы полной и бесполезной: задача запускается ИМЕННО
    оттуда — окружение ставится в кэш узла и переиспользуется. Разрешения на
    чтение для запуска достаточно, но проверить это надо, а не предположить:
    цена ошибки — узел, который принимает задачи и не может их начать."""
    root, task, scratch = node
    bindir = root / "envs" / "e1" / "bin"
    bindir.mkdir(parents=True)
    launcher = bindir / "run.sh"
    launcher.write_text("#!/bin/sh\necho started\n")
    launcher.chmod(0o755)

    profile = sandbox.prepare(task_dir=task, scratch=scratch,
                              envs_dir=root / "envs", models_dir=root / "models",
                              agent_root=root)
    done = subprocess.run(
        sandbox.wrap([str(launcher)], profile_path=profile, task_dir=task,
                     scratch=scratch, envs_dir=root / "envs",
                     models_dir=root / "models", agent_root=root),
        capture_output=True, text=True, timeout=60)

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "started"


def test_настоящая_задача_не_видит_ключ_узла(tmp_path, monkeypatch):
    """Сквозь весь путь, а не только через модуль песочницы.

    Проверка модуля говорит, что политика верна. Она ничего не говорит о том,
    доехала ли эта политика до `subprocess.Popen` — а между ними лежат реестр,
    каталог задачи и сборка команды, и потеряться там можно бесшумно.
    """
    monkeypatch.setenv("LOOMA_ALLOW_UNPRIVILEGED_TASKS", "1")
    from looma_agent.tasks.env import EnvironmentCache
    from looma_agent.tasks.limits import resolve_isolation
    from looma_agent.tasks.registry import TaskRegistry
    from looma_agent.tasks.spec import TaskSpec

    root = tmp_path / "looma"
    (root / "tasks").mkdir(parents=True)
    (root / "secret.key").write_text("ключ узла")
    registry = TaskRegistry(root=root / "tasks", isolation=resolve_isolation(),
                            environments=EnvironmentCache(root / "envs"),
                            total_gpus=0, retention_s=60.0)
    try:
        task = registry.submit(TaskSpec.from_dict({
            "task_id": "probe",
            "command": [SYSTEM_PYTHON, "-c",
                        f"open({str(root / 'secret.key')!r}).read()"],
        }))
        assert task.wait(timeout=60)
        assert task.state == "failed", "задача прочитала ключ узла"
        assert "Permission" in task.logs() or "Errno 1" in task.logs(), task.logs()
    finally:
        registry.stop_all()


def test_дом_хозяина_закрыт_у_настоящей_установки(tmp_path, monkeypatch):
    """Ради этого всё и делается: на чужом Mac в /Users лежит вся жизнь
    владельца, и задача про неё знать не должна."""
    monkeypatch.setattr(sandbox.sys, "executable", "/Library/Looma/python/bin/python3")
    monkeypatch.setattr(sandbox.sys, "prefix", "/Library/Looma/python")
    monkeypatch.setattr(sandbox.sys, "base_prefix", "/Library/Looma/python")

    assert sandbox.guards_home(Path("/Library/Application Support/Looma"))
    text = sandbox.profile(task_dir=tmp_path, scratch=tmp_path, envs_dir=tmp_path,
                           models_dir=tmp_path, agent_root=tmp_path)
    assert '(deny file-read* file-write* (subpath "/Users"))' in text


def test_дом_не_закрывается_если_агент_живёт_в_нём(tmp_path, monkeypatch):
    """Иначе узел берёт задачи и не может их начать: интерпретатор оказывается
    по ту сторону границы, а execvp говорит «Operation not permitted», из чего
    причина не следует никак.

    Остальные границы при этом остаются — закрыт корень агента, чужие задачи и
    запись в кэш окружений."""
    monkeypatch.setattr(sandbox.sys, "executable", "/Users/dev/.venv/bin/python")

    assert not sandbox.guards_home(Path("/Library/Application Support/Looma"))
    text = sandbox.profile(task_dir=tmp_path, scratch=tmp_path, envs_dir=tmp_path,
                           models_dir=tmp_path, agent_root=tmp_path)
    assert '(subpath "/Users")' not in text
    assert '(deny file-read* file-write* (subpath (param "LOOMA_ROOT")))' in text
