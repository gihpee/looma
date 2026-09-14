"""Where a command from the orchestrator goes.

A dispatcher and nothing else: it decides which part of the agent a message
belongs to and does none of the work itself. Anything that could take time
happens on a thread the task machinery owns, because a stream that stops being
read makes this node look dead.
"""

from __future__ import annotations

import logging
import threading

from looma_agent.control.tasks import TaskCommands
from looma_agent.proto import agent_pb2

logger = logging.getLogger("looma_agent.handlers")

# Команды, которые нельзя выполнять в приёмном потоке. Мерки простые: снятие
# ждёт процесс до GRACE_S, освобождение — до 30 секунд плюс удаление каталога
# задачи и обход кэша весов. Всё остальное здесь либо кладёт в очередь, либо
# отвечает сразу; порядок для них важен (куски входных файлов, куски туннеля),
# и уводить их в потоки нельзя — приедут вперемешку.
SLOW = frozenset({"stop_task", "release_task"})


class CommandHandlers:
    def __init__(self, *, tasks: TaskCommands, telemetry, on_release=None,
                 on_node_command=None) -> None:
        self.tasks = tasks
        self._telemetry = telemetry
        # Подсказка «посмотри на версию сейчас». Приходит, когда оператор
        # перевёл ступень, а этот узел никуда не переподключался.
        self.on_release = on_release or (lambda _release: None)
        # Команда узлу, а не задаче: перечитать железо или перезапуститься.
        # Мимо задач, поэтому и мимо TaskCommands — распоряжается этим сам
        # агент, у которого и железо, и код выхода.
        self.on_node_command = on_node_command or (lambda _command: None)

    def handle(self, message: agent_pb2.ServerMessage) -> None:
        kind = message.WhichOneof("msg")
        if kind in SLOW:
            # Своим потоком, а не здесь. Этот метод зовётся ПРЯМО из цикла
            # приёма управляющего потока, и пока он не вернётся, агент не
            # разбирает ни одной другой команды.
            #
            # Снятие ждёт процесс до GRACE_S, освобождение — до 30 секунд, плюс
            # удаление каталога задачи и обход кэша весов. С дампа живого агента
            # (py-spy) главный поток стоял в снятии, а весь приём стоял вместе с
            # ним. Узел в это время глухой: ни второй команды, ни ответа на
            # запрос логов — то есть неотличим от мёртвого.
            threading.Thread(target=self._carry, args=(kind, message),
                             name=f"cmd-{kind}", daemon=True).start()
            return
        self._dispatch(kind, message)

    def _carry(self, kind: str, message: agent_pb2.ServerMessage) -> None:
        try:
            self._dispatch(kind, message)
        except Exception:
            logger.exception("команда %s не отработала", kind)

    def _dispatch(self, kind: str, message: agent_pb2.ServerMessage) -> None:
        if kind == "input_chunk":
            self.tasks.input_chunk(message.input_chunk)
        elif kind == "run_task":
            self.tasks.run(message.run_task)
        elif kind == "stop_task":
            self.tasks.stop(message.stop_task)
        elif kind == "release_task":
            self.tasks.release(message.release_task)
        elif kind == "fetch_result":
            self.tasks.fetch_result(message.fetch_result)
        elif kind == "fetch_logs":
            self.tasks.fetch_logs(message.fetch_logs)
        elif kind == "task_message":
            self.tasks.on_task_message(message.task_message)
        elif kind == "task_request":
            self.tasks.task_request(message.task_request)
        elif kind == "tunnel_open":
            self.tasks.tunnel_open(message.tunnel_open)
        elif kind == "tunnel_chunk":
            self.tasks.tunnel_chunk(message.tunnel_chunk)
        elif kind == "release":
            self.on_release(message.release)
        elif kind == "node_command":
            self.on_node_command(message.node_command)
        elif kind == "probe":
            self.tasks.send(self._telemetry())
        else:
            # Loud, not silent: a node that quietly drops commands looks
            # healthy while doing nothing, which is the worst way to fail.
            logger.error("no handler for %r; this agent and the orchestrator "
                         "disagree about the protocol", kind)
