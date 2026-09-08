"""Run the agent, and keep running it.

The agent is a subprocess. When it exits, the launcher starts it again — the
node owner asked for a machine that stays connected, not for one that quits on
the first unhandled error.

It also installs what the agent downloaded and takes it back out again when it
does not work. "Stood up" means the agent reached the orchestrator and said so
by writing a health marker — not that the process is alive, because a payload
that starts, fails to connect and sits there is alive and useless.

The rollback decision is made HERE, on the node, without asking anyone: the
connection to the orchestrator may be exactly what the new version broke.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from typing import List, Optional

from looma_launcher import payload as payload_mod
from looma_launcher.payload import Payload

logger = logging.getLogger("looma_launcher.supervise")

# Long enough that a crash loop does not spin the CPU, short enough that a
# transient failure does not take the node out of the fleet for minutes.
RESTART_DELAY_S = 3.0
# A restart this soon after start means the agent never really came up.
TOO_SOON_S = 30.0
# How many fast failures of a version that has never once registered before we
# put the previous one back. Two rather than one: a single crash can be the
# machine (a card gone, a full disk), and rolling back would not fix it.
FAILURES_BEFORE_ROLLBACK = 3
# Этим кодом агент говорит, что вышел нарочно — скачал обновление и уступает
# место. Обычный ноль от этого не отличить, а считать плановую остановку
# падением значит подвести исправную версию под откат.
UPDATE_EXIT_CODE = 70
# Откуда запускать агента и с какими флагами.
#
# `-P` и каталог вне /app — против одной и той же ошибки, и она стоила нам
# нескольких дней. `python -m` кладёт ТЕКУЩИЙ каталог первым в sys.path,
# впереди PYTHONPATH. Рабочий каталог образа — /app, а там лежит looma_agent,
# скопированный для сборки. Значит payload проигрывал образу всегда: лаунчер
# его ставил, версию объявлял, а исполнялся код из образа.
#
# Хуже всего то, как это выглядело: панель показывала новую версию, узлы
# отчитывались, что обновились, и ни одной ошибки нигде. Просто ничего не
# менялось.
AGENT_CWD = "/"
AGENT_FLAGS = ["-P"]


def _task_user() -> str:
    """Служебный пользователь этой системы, если он заведён.

    Проверяем существование, а не подставляем вслепую: имя, которого на машине
    нет, ничем не лучше отсутствующего, зато скрывает настоящую причину за
    другой.
    """
    if sys.platform != "darwin":
        return ""
    import pwd

    for name in ("_looma", "looma-task"):
        try:
            pwd.getpwnam(name)
            return name
        except KeyError:
            continue
    return ""


def _why_updates_are_off() -> str:
    """Пусто, если обновления возможны."""
    from looma_launcher.signature import public_key_bytes

    if public_key_bytes() is not None:
        return ""
    return ("в образе этого узла нет ключа релизов, проверить подпись нечем — "
            "обновления по сети выключены. Лечится только пересборкой образа "
            "с ключом и docker pull на узле")


class Supervisor:
    def __init__(self, payload: Payload, agent_args: List[str]) -> None:
        self.payload = payload
        self.agent_args = agent_args
        self.consecutive_failures = 0
        self._proc: Optional[subprocess.Popen] = None
        self._stop = threading.Event()

    def run_forever(self) -> int:
        self._install_signal_handlers()
        logger.info("launcher starting agent %s", self.payload.describe())
        while not self._stop.is_set():
            self._wait_while_paused()
            if self._stop.is_set():
                return 0
            self._apply_downloaded()
            self._say(running=True)
            started = time.monotonic()
            code = self._run_once()
            if self._stop.is_set():
                return code or 0
            lived = time.monotonic() - started
            if code == UPDATE_EXIT_CODE:
                logger.info("agent %s stepped aside for an update", self.payload.version)
                self.consecutive_failures = 0
                continue
            if lived < TOO_SOON_S:
                self.consecutive_failures += 1
                logger.warning(
                    "agent %s exited after %.1fs with code %s (%d in a row)",
                    self.payload.version, lived, code, self.consecutive_failures,
                )
                self._consider_rollback()
            else:
                self.consecutive_failures = 0
                logger.warning("agent exited with code %s after %.0fs", code, lived)
            time.sleep(RESTART_DELAY_S)
        return 0

    # ------------------------------------------------ разрешение владельца
    def _paused(self) -> bool:
        return (payload_mod.root() / "paused").exists()

    def _wait_while_paused(self) -> None:
        """Стоять, пока владелец машины не разрешит снова.

        ЗДЕСЬ, а не в агенте, и это главное. Агент приезжает по сети, и его
        версия — та, что выпущена, а не та, что лежит в пакете. Со стенда:
        кнопка «остановить» появилась в коде агента, узел обновился до релиза
        без неё, и кнопка перестала действовать — при том, что файл она
        создавала исправно. Пусковой слой меняется только вместе с пакетом,
        поэтому управление узлом должно быть в нём.
        """
        if not self._paused():
            return
        logger.info("узел остановлен владельцем; жду, пока уберут файл paused")
        while self._paused() and not self._stop.is_set():
            self._say(running=False)
            self._stop.wait(2.0)
        if not self._stop.is_set():
            logger.info("узел снова разрешён")

    def _say(self, *, running: bool) -> None:
        """Рассказать панели, что происходит, — от лица пускового слоя.

        Агент пишет свой снимок сам, но только тот, что умеет; вышедший или
        остановленный не пишет ничего, и панель показывала бы последний
        снимок вечно. Здесь пишется то, что пусковой слой знает точно:
        какая версия запущена и работает ли она вообще.
        """
        try:
            from looma_agent import status
        except ImportError:
            return
        status.write(payload_mod.root(), {
            "launcher": True,
            "running": running,
            "paused": self._paused(),
            "agent_version": self.payload.version,
            "why": "" if running else "узел остановлен владельцем машины",
            "updated_at": time.time(),
        }, name="launcher.json")

    # ---------------------------------------------------------------- updates
    def _apply_downloaded(self) -> None:
        """Install whatever the agent fetched before it stopped.

        Verification happens inside install(). The agent downloads; it does not
        decide what may run — that is the whole reason this code is in the part
        an update cannot replace.
        """
        for manifest in payload_mod.pending():
            installed = payload_mod.install(
                manifest, installed_version=self.payload.version)
            if installed is None:
                continue
            payload_mod.switch_to(installed)
            self.payload = installed
            self.consecutive_failures = 0
            logger.info("now running agent %s", installed.describe())

    def _consider_rollback(self) -> None:
        if self.consecutive_failures < FAILURES_BEFORE_ROLLBACK:
            return
        if self.payload.bundled:
            # Already at the floor. Keep restarting: the problem is not the
            # payload, and there is nothing better to fall back to.
            return
        if payload_mod.health_marker(self.payload.version).exists():
            # It worked before, so the new thing is not what broke. Rolling
            # back would hide a real problem behind a version change.
            logger.error("agent %s keeps failing although it once registered; "
                         "not rolling back", self.payload.version)
            return
        logger.error("agent %s never registered and failed %d times; going back",
                     self.payload.version, self.consecutive_failures)
        # Запомнить отказ ОБЯЗАТЕЛЬНО, иначе откат ничего не решает: агент
        # прошлой версии поднимется, получит от оркестратора то же предложение,
        # скачает ту же версию, она снова упадёт три раза — и так по кругу,
        # вечно. Со стенда: цикл повторялся каждые двенадцать секунд, и со
        # стороны это выглядело как «узел то появляется, то пропадает».
        #
        # Раньше отказ записывался только при неудачной УСТАНОВКЕ — битый архив,
        # чужая подпись. Версия, которая ставится и падает, не попадала в него
        # никогда.
        payload_mod.remember_refusal(
            self.payload.version,
            f"версия падала {self.consecutive_failures} раз подряд и ни разу "
            "не подключилась к оркестратору")
        restored = payload_mod.roll_back()
        if restored is not None:
            self.payload = restored
            self.consecutive_failures = 0

    def _run_once(self) -> Optional[int]:
        env = dict(os.environ)
        if self.payload.path is not None:
            # Payload впереди всего, что стоит в образе, — чтобы обновление
            # действительно применялось. Одного этого мало: см. AGENT_CWD.
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{self.payload.path}{os.pathsep}{existing}" if existing else str(self.payload.path)
        env["LOOMA_AGENT_VERSION"] = self.payload.version
        # Where the agent says it actually came up. Its absence after repeated
        # fast exits is what triggers a rollback.
        env["LOOMA_AGENT_HEALTH_FILE"] = str(payload_mod.health_marker(self.payload.version))
        env["LOOMA_AGENT_INCOMING"] = str(payload_mod.incoming_dir())
        # Под кем агенту запускать чужие задачи. Имя зависит от системы: на
        # macOS у служебных пользователей оно начинается с подчёркивания, и
        # установщик заводит `_looma`. Агент, приехавший по сети, может знать
        # только линуксовое `looma-task` — тогда он не найдёт пользователя и
        # откажется брать работу совсем. Со стенда именно так и вышло: узел
        # подключился и стоял со словами «не берёт».
        task_user = _task_user()
        if task_user and "LOOMA_TASK_USER" not in env:
            env["LOOMA_TASK_USER"] = task_user
        # Образ без ключа не примет ни одного релиза. Сказать об этом агенту
        # сейчас — значит увидеть причину в панели; промолчать — значит
        # смотреть, как узел качает, сливается и перезапускается по кругу.
        blocked = _why_updates_are_off()
        if blocked:
            env["LOOMA_UPDATES_DISABLED"] = blocked
        argv = [sys.executable, *AGENT_FLAGS, "-m", "looma_agent.main", *self.agent_args]
        self._proc = subprocess.Popen(argv, env=env, cwd=AGENT_CWD,
                                      start_new_session=True)
        # Пока агент работает, следим за файлом paused: остановить его надо и
        # на ходу, а не только перед запуском. Иначе кнопка действует лишь
        # после того, как агент почему-то сам перезапустится.
        watcher = threading.Thread(target=self._stop_when_paused,
                                   name="pause-watch", daemon=True)
        watcher.start()
        heartbeat = threading.Thread(target=self._keep_saying,
                                     name="launcher-status", daemon=True)
        heartbeat.start()
        try:
            return self._proc.wait()
        except KeyboardInterrupt:
            return None

    def _keep_saying(self) -> None:
        """Обновлять снимок, пока агент жив.

        Разово его мало: панель считает узел замолчавшим, если снимку больше
        полуминуты, — и правильно делает, иначе умерший агент выглядел бы
        работающим вечно. Со стенда: панель показывала «узел работает», а через
        несколько секунд «агент замолчал», потому что снимок был написан один
        раз при запуске.
        """
        proc = self._proc
        while proc is not None and proc.poll() is None and not self._stop.is_set():
            self._say(running=True)
            time.sleep(5.0)

    def _stop_when_paused(self) -> None:
        proc = self._proc
        while proc is not None and proc.poll() is None:
            if self._stop.is_set():
                return
            if self._paused():
                logger.info("владелец остановил узел; снимаю агента")
                try:
                    proc.terminate()
                except OSError:
                    pass
                return
            time.sleep(2.0)

    # ------------------------------------------------------------- shutdown
    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_signal)

    def _on_signal(self, signum, _frame) -> None:
        # `docker stop` sends SIGTERM to the launcher only. Without passing it
        # on, the agent would be killed by the timeout instead of shutting
        # down, and whatever it was doing would be lost rather than finished.
        logger.info("launcher got signal %s; stopping the agent", signum)
        self._stop.set()
        self._terminate_agent()

    def _terminate_agent(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            logger.warning("agent did not stop in time; killing it")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
