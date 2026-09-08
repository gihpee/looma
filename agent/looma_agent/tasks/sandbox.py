"""Песочница macOS: то же, что на Linux даёт контейнер.

На Linux задача заперта дважды — отдельным пользователем внутри контейнера и
самим контейнером, у которого своя файловая система. На пользовательском Mac
контейнера нет и быть не может: Metal в него не пробрасывается, а без Metal
машина Apple перестаёт быть тем, ради чего её брали.

Отдельный пользователь остаётся и здесь. Не хватает второй половины — границы
по файловой системе, — и её даёт `sandbox-exec`: тот же механизм, которым
macOS запирает свои демоны.

Что проверено на стенде (macOS 26.5), а не взято из документации:

  * ключ узла и дом хозяина недоступны задаче;
  * свой каталог доступен на запись;
  * кэш окружений — только на чтение, каталог соседней задачи — никак;
  * `allow` после более широкого `deny` перекрывает его: в SBPL выигрывает
    последнее совпавшее правило, и вся политика ниже на этом стоит.

Об одном стоит знать заранее: формально API объявлен устаревшим ещё в 10.14 и
с тех пор никуда не делся — на нём держится половина системы. Если он однажды
исчезнет, отдельный пользователь останется, и узел не станет опасным: он
станет менее изолированным, о чём тогда придётся сказать вслух.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("looma_agent.tasks.sandbox")

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

# Куда задаче нельзя ни при каких условиях. Дом хозяина — первое: машина
# одолжена, а не отдана, и содержимое Documents к вычислениям отношения не
# имеет.
FORBIDDEN = ("/Users", "/Volumes")
# Что менять нельзя, но читать нужно: интерпретатор, системные библиотеки,
# сертификаты.
READ_ONLY = ("/usr", "/bin", "/sbin", "/etc", "/opt", "/Library", "/System",
             "/Applications")


def available() -> bool:
    """Можно ли здесь запереть задачу этим способом."""
    return platform.system() == "Darwin" and os.path.exists(SANDBOX_EXEC)


def guards_home(agent_root: Path) -> bool:
    """Закрывать ли дом хозяина целиком.

    Закрывать — цель всей затеи: на чужом Mac в `/Users` лежит вся жизнь
    владельца, а задача про неё знать не должна. Но закрыть его можно только
    там, где НАШЕГО в доме ничего нет: ни агента, ни питона, которым он
    запущен.

    Иначе получается узел, который берёт задачи и не может их начать. Разбор
    этого стоил отдельного вечера: `.venv/bin/python` оказался симлинком на
    `cpython-3.12-…`, тот — на `cpython-3.12.13-…`, и разрешение по любому из
    трёх путей не спасало, потому что цепочку надо открыть целиком. Гадать,
    где стоит интерпретатор — в uv, в pyenv, в системе, — занятие без дна.

    Поэтому критерий простой и проверяемый: дом закрывается, когда мы в нём не
    живём. У настоящей установки провайдера так и есть — агент и его питон
    лежат в /Library. У разработчика наоборот, и тогда остальные границы
    (корень агента, чужие задачи, кэш окружений) работают по-прежнему, а про
    дом сказано вслух.
    """
    ours = [Path(agent_root), Path(sys.executable), Path(sys.prefix),
            Path(sys.base_prefix)]
    for path in ours:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if any(str(resolved).startswith(home + os.sep) for home in FORBIDDEN):
            return False
    return True


def profile(*, task_dir: Path, scratch: Path, envs_dir: Path,
            models_dir: Path, agent_root: Path) -> str:
    """Политика для одной задачи.

    Список путей, а не одна большая дырка: разница между «задача пишет в свой
    каталог» и «задача пишет в каталог агента» — это разница между рабочим
    узлом и узлом, с которого унесли ключ.

    Порядок правил — часть смысла. Сначала закрывается всё лишнее целиком,
    потом по одному открывается выданное; переставить их местами значит
    открыть корень агента полностью.
    """
    lines = [
        "(version 1)",
        # Не «запретить всё и открывать по одному»: задача — чужой код на
        # любом языке, ему нужны сотни системных вызовов, и полный список
        # означал бы отказы в местах, о которых мы узнаем от клиента. Здесь
        # закрывается то, что назвать МОЖНО и нужно: чужие файлы.
        "(allow default)",
        "",
        ";; Дом хозяина и съёмные диски — мимо задачи.",
        *(f'(deny file-read* file-write* (subpath "{path}"))'
          for path in (FORBIDDEN if guards_home(agent_root) else ())),
        "",
        ";; Каталог агента: там ключ узла, его payload и задачи соседей.",
        '(deny file-read* file-write* (subpath (param "LOOMA_ROOT")))',
        "",
        ";; Системное — читать можно, менять нельзя.",
        *(f'(deny file-write* (subpath "{path}"))' for path in READ_ONLY),
        "",
        ";; И только теперь — то, что выдано этой задаче.",
        '(allow file-read* file-write* (subpath (param "LOOMA_TASK")))',
        '(allow file-read* file-write* (subpath (param "LOOMA_SCRATCH")))',
        # Кэш окружений общий на узел и переиспользуется следующими задачами:
        # запись в него означала бы, что одна задача подкладывает библиотеку
        # следующей.
        '(allow file-read* (subpath (param "LOOMA_ENVS")))',
        # Кэш весов, наоборот, задача пополняет: модель качается один раз на
        # узел, и качает её тот, кому она понадобилась первым.
        '(allow file-read* file-write* (subpath (param "LOOMA_MODELS")))',
        "",
    ]
    return "\n".join(lines)


def wrap(command: List[str], *, profile_path: Path, task_dir: Path,
         scratch: Path, envs_dir: Path, models_dir: Path,
         agent_root: Path) -> List[str]:
    """Обернуть команду задачи в песочницу.

    Пути передаются параметрами, а не подставляются в текст профиля: путь с
    кавычкой или скобкой сломал бы разбор, и сломал бы его в сторону «профиль
    не применился».
    """
    return [
        SANDBOX_EXEC, "-f", str(profile_path),
        "-D", f"LOOMA_ROOT={agent_root}",
        "-D", f"LOOMA_TASK={task_dir}",
        "-D", f"LOOMA_SCRATCH={scratch}",
        "-D", f"LOOMA_ENVS={envs_dir}",
        "-D", f"LOOMA_MODELS={models_dir}",
        *command,
    ]


def prepare(*, task_dir: Path, scratch: Path, envs_dir: Path, models_dir: Path,
            agent_root: Path) -> Optional[Path]:
    """Записать профиль рядом с задачей и вернуть путь к нему.

    Рядом, но НЕ внутри `work`: там хозяйничает сама задача, и профиль,
    который она может переписать, — это профиль, которого нет. На следующий
    запуск, впрочем, подмена всё равно не влияет: профиль читается до старта.

    None означает «здесь так нельзя» — тогда задача остаётся при одном
    отдельном пользователе, и решать, годится ли это, будет вызывающий.
    """
    if not available():
        return None
    path = task_dir / "sandbox.sb"
    try:
        path.write_text(profile(task_dir=task_dir, scratch=scratch,
                                envs_dir=envs_dir, models_dir=models_dir,
                                agent_root=agent_root))
        # Читать может кто угодно, менять — только агент.
        path.chmod(0o644)
    except OSError as exc:
        logger.warning("профиль песочницы не записался (%s); задача пойдёт без неё", exc)
        return None
    return path


def which_sandbox() -> str:
    """Чем именно запираем — для строки в логе при запуске узла."""
    return shutil.which(SANDBOX_EXEC) or ""
