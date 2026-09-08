"""Assembly. Read this first — every connection in the agent is visible here.

    join key  ──→ identity        where to call, and the secret to verify with
    machine   ──→ hwinfo          what this node has to offer
                     │
              control/client      one outbound stream, held open
                     │
              control/handlers    what to do with a command
                     │
              tasks/              (phase 1: directories, environments, running)

Phase 0 stops after the stream: the node registers, reports itself, and refuses
work it cannot do yet. That is a complete, useful thing — it proves onboarding,
reconnection and the image size before anything harder is built on top.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time

from looma_agent import __version__
from looma_agent.config import Config, parse_args
from looma_agent.control.client import ControlClient
from looma_agent.control.handlers import CommandHandlers
from looma_agent.hwinfo import (
    cuda_driver_version,
    detect_hardware,
    disk_bytes,
    free_vram_bytes,
)
from looma_agent.identity import BadJoinKey, default_node_id, parse_join_key
from looma_agent.p2p.layer import PeerLayer
from looma_agent.tasks.env import EnvironmentCache
from looma_agent.tasks.env.cache import BUILDERS as ENVIRONMENT_KINDS
from looma_agent.tasks.limits import resolve_isolation
from looma_agent.tasks.models import ModelCache
from looma_agent.tasks.registry import TaskRegistry
from looma_agent.update import Updater, mark_healthy
from looma_agent.control.tasks import TaskCommands
from looma_agent import recent
from looma_agent.proto import agent_pb2

logger = logging.getLogger("looma_agent")


def _setup_logging() -> None:
    формат = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(
        level=os.environ.get("LOOMA_LOG_LEVEL", "INFO").upper(),
        format=формат,
    )
    # Рядом с выводом, а не вместо: `docker logs` на самой машине должен
    # работать как раньше, а оператору нужен тот же текст издалека.
    recent.install(logging.Formatter(формат))


def hardware_message() -> agent_pb2.Hardware:
    """What this machine has, detected — never declared by its owner.

    Logged before it is sent: when a node turns out not to fit a model, this
    line is the first thing worth looking at, and `detection_source` says which
    of NVML / torch / nvidia-smi / sysctl actually answered.
    """
    hw = detect_hardware()
    driver = cuda_driver_version()
    logger.info(
        "hardware: device=%s gpu=%s x%d vram_free=%.1fGB tflops=%.1f (%s)",
        hw.device, hw.gpu_name, hw.num_gpus,
        hw.vram_free_bytes / 1024**3, hw.tflops_fp16, hw.detection_source,
    )
    return agent_pb2.Hardware(
        num_gpus=hw.num_gpus,
        tflops_fp16=hw.tflops_fp16,
        gpu_name=hw.gpu_name,
        memory_gb=hw.memory_gb,
        memory_bandwidth_gbps=hw.memory_bandwidth_gbps,
        device=hw.device,
        vram_free_bytes=hw.vram_free_bytes,
        vram_total_bytes=hw.vram_total_bytes,
        host_ram_gb=hw.host_ram_gb,
        detection_source=hw.detection_source,
        cuda_version=f"{driver[0]}.{driver[1]}" if driver else "",
    )


class Agent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.key = parse_join_key(config.join_key)
        self.node_id = config.node_id or default_node_id()
        self.hardware = hardware_message()
        self._stop = threading.Event()
        self.isolation = resolve_isolation()
        self.tasks = TaskRegistry(
            root=config.tasks_dir,
            isolation=self.isolation,
            environments=EnvironmentCache(config.envs_dir),
            models=ModelCache(config.models_dir),
            total_gpus=self.hardware.num_gpus,
        )
        # Inbound direct messages go to the task machinery, which is built
        # just below — hence the late binding rather than a direct reference.
        self.peers = PeerLayer(on_message=lambda raw: self.commands.on_peer_message(raw))
        self.client = ControlClient(
            address=self.key.address,
            tls=self.key.tls,
            register_message=self._register_message,
            on_message=lambda msg: self.handlers.handle(msg),
            on_registered=self._on_registered,
            reconnect_delay_s=config.reconnect_delay_s,
        )
        self.commands = TaskCommands(
            registry=self.tasks, send=self.client.send,
            node_id=self.node_id, links=self.peers.links,
            # Отдельно от links: та возит сообщения и про узел ничего не знает,
            # а байтовый туннель открывается на самом узле.
            peers=self.peers,
        )
        self.updater = Updater(
            current_version=__version__,
            drain=self.tasks.drain,
            stop=self.stop,
        )
        self.handlers = CommandHandlers(tasks=self.commands, telemetry=self._telemetry,
                                        on_release=lambda r: self.updater.on_release(r),
                                        on_node_command=self._node_command)

    def _on_registered(self, ack: agent_pb2.RegisterAck) -> None:
        """The ack carries the one address this node needs to reach every other.

        Bootstrap peers are fixed when a libp2p node is built, so the p2p node
        cannot come up before now. It is started on the ack rather than on the
        first piece of work: joining the network takes seconds, and a
        deployment should not be the thing that waits for it.
        """
        self.peers.on_rendezvous(list(ack.rendezvous), list(ack.relays))
        # Only now: reaching the orchestrator is what "this payload works"
        # means, and the launcher's rollback reads exactly this.
        mark_healthy()
        self.updater.on_release(ack.release)

    def _register_message(self) -> agent_pb2.Register:
        message = agent_pb2.Register(
            node_id=self.node_id,
            join_key=self.key.raw,
            hardware=self.hardware,
            region=self.config.region,
            agent_version=__version__,
            readiness=self._readiness(),
        )
        # Absent on a first registration and present after a reconnect: by then
        # the node knows its own peer id and addresses, and the orchestrator can
        # hand them to its neighbours.
        identity = self.peers.identity_message()
        if identity is not None:
            message.peer.peer_id = identity.peer_id
            message.peer.listen_addrs.extend(identity.listen_addrs)
            message.peer.symmetric_nat = bool(identity.symmetric_nat)
        return message

    def _readiness(self) -> agent_pb2.Readiness:
        """What this node can actually do, said at registration.

        A node that cannot isolate a task declares it rather than accepting
        work and failing every time — the orchestrator can then place around it
        instead of discovering the problem one task at a time.
        """
        refusal = self.tasks.unusable
        if not refusal and not self.isolation.drops_privileges and \
                not self.isolation.unprivileged_fallback:
            refusal = "this node cannot run tasks as a separate user"
        return agent_pb2.Readiness(
            accepts_tasks=not refusal,
            refusal=refusal,
            environment_kinds=sorted(["none", *ENVIRONMENT_KINDS]),
        )

    # -------------------------------------------------------- команды узлу
    def _node_command(self, command: agent_pb2.NodeCommand) -> None:
        """Что оператор попросил сделать с самим узлом.

        На своей нити: детект железа ходит к nvidia-smi, а перезапуск сливает
        задачи минутами. Держать этим управляющий стрим нельзя — узел, который
        перестал читать команды, снаружи неотличим от мёртвого.
        """
        threading.Thread(target=self._do_node_command, args=(command,),
                         name="node-command", daemon=True).start()

    def _do_node_command(self, command: agent_pb2.NodeCommand) -> None:
        action = (command.action or "").strip()
        if action == "restart":
            # Ответ ДО слива задач, а не после: слив длится до десяти минут, и
            # оператор всё это время не знал бы даже, дошла ли команда. Уехать
            # он успевает: остановка кладёт своё «закрываемся» в ТУ ЖЕ очередь,
            # а отправитель выгребает её по порядку.
            self._answer(command, True, "")
            self.updater.step_aside("перезапуск по команде оператора")
            return
        if action == "rescan":
            self._answer(command, *self._rescan())
            return
        # Узел старее оркестратора: молча принять незнакомое действие значит
        # оставить оператора с кнопкой, которая ничего не делает и об этом не
        # говорит.
        self._answer(command, False,
                     f"этот агент не знает действия {action!r}; нужна версия новее")

    def _answer(self, command: agent_pb2.NodeCommand, ok: bool, why: str) -> None:
        if not ok:
            logger.warning("отказ на %s: %s", command.action, why)
        self.client.send(agent_pb2.AgentMessage(ack=agent_pb2.Ack(
            command_id=command.command_id, ok=ok, error=why)))

    def _rescan(self) -> tuple:
        """Перечитать железо, не останавливая ничего.

        Ради этого всё и делалось: детект идёт один раз, в конструкторе, и
        живёт до конца процесса. Карта, вернувшаяся после того, как её хозяин
        пересобрал драйвер, не появлялась нигде — а единственным способом
        перезапустить агента на ЧУЖОЙ машине была выкатка релиза.
        """
        fresh = hardware_message()
        refusal = self.tasks.recount_devices(fresh.num_gpus)
        if refusal:
            return False, refusal
        было = f"{self.hardware.device} x{self.hardware.num_gpus}"
        self.hardware = fresh
        logger.info("железо перечитано по команде: было %s, стало %s x%d (%s)",
                    было, fresh.device, fresh.num_gpus, fresh.detection_source)
        # Сразу, а не со следующим ударом сердца: оператор нажал кнопку и
        # смотрит на экран именно теперь.
        if self.client.registered:
            self.client.send(self._telemetry())
        return True, ""

    def _telemetry(self) -> agent_pb2.AgentMessage:
        snapshot = self.tasks.snapshot()
        # Про том, где лежат кэши и задачи, а не про машину: вытеснение считает
        # квоты именно от него.
        free_disk, total_disk = disk_bytes(self.config.root)
        report = agent_pb2.Telemetry(
            node_id=self.node_id,
            vram_free_bytes=self.hardware.vram_free_bytes,
            reported_at_unix_ms=int(time.time() * 1000),
            gpus_total=snapshot["gpus_total"],
            gpus_free=snapshot["gpus_free"],
            tasks_running=snapshot["running"],
            env_cache_bytes=snapshot["environments"]["bytes"],
            model_cache_bytes=(snapshot["models"] or {}).get("bytes", 0),
            disk_free_bytes=free_disk,
            disk_total_bytes=total_disk,
            # Железо в каждой телеметрии, а не только при регистрации: иначе
            # панель показывает машину такой, какой она была в минуту
            # подключения, и пересчёт по кнопке некуда было бы доложить.
            hardware=self.hardware,
            # Идёт ли трафик к соседям напрямую. Без этого «конвейер тормозит»
            # и «каждая активация едет длинным путём» выглядят одинаково.
            peer=self.peers.status(),
            update=self.updater.status(),
        )
        for task in self.tasks.list():
            status = task.status()
            report.tasks.add(
                task_id=status["task_id"], state=status["state"],
                exit_code=status["exit_code"] or 0, error=status["error"],
                devices=list(status["devices"]), seconds=status["seconds"],
            )
        # Взятые, но ещё не запущенные — тоже наши. Перепись без них означает
        # «этой задачи у меня нет», и оркестратор списывает её как пропавшую,
        # пока мы ставим для неё окружение.
        for task_id in self.tasks.claimed():
            report.tasks.add(task_id=task_id, state="provisioning")
        return agent_pb2.AgentMessage(telemetry=report)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.config.heartbeat_interval_s):
            self._refresh_vram()
            if self.client.registered:
                self.client.send(self._telemetry())

    def _refresh_vram(self) -> None:
        """Пересчитать свободную VRAM: снимок при старте устареет за минуту.

        Ноль от измерителя означает «не смогли», а не «памяти нет», поэтому
        прежнее значение остаётся: подставить ноль — значит вывести рабочий
        узел из планирования из-за одного неудачного опроса.
        """
        free = free_vram_bytes()
        if free:
            self.hardware.vram_free_bytes = free

    def _report_readiness(self) -> None:
        """Say once, at startup, whether this node can actually take work.

        A node that refuses every task looks identical to a healthy idle one
        from the outside. Saying so here means the owner finds out from their
        own logs rather than from a support thread a week later.
        """
        if self.tasks.unusable:
            logger.warning("this node will refuse every task: %s", self.tasks.unusable)
            return
        if self.isolation.drops_privileges:
            logger.info("tasks will run as %s on %d GPU(s)",
                        self.isolation.user, self.hardware.num_gpus)
        elif self.isolation.unprivileged_fallback:
            logger.warning("tasks will run as this agent's own user: weaker isolation "
                           "was accepted explicitly on this node")
        else:
            logger.warning("this node will REFUSE every task until it can run them as "
                           "a separate user; it will still report itself and idle")

    def run(self) -> int:
        logger.info("agent %s: node %s -> %s", __version__, self.node_id, self.key.address)
        self._report_readiness()
        self.commands.start()
        threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True).start()
        self.client.run_forever()
        # Ненулевой код здесь означает «остановился ради обновления», а не
        # поломку: пусковой слой не должен считать это падением.
        return self.updater.exit_code

    def stop(self) -> None:
        self._stop.set()
        # Tasks first: they are somebody's work, and stopping them politely
        # while the stream is still up means the orchestrator hears why.
        self.tasks.stop_all()
        self.commands.shutdown()
        self.client.stop()


def main(argv=None) -> int:
    _setup_logging()
    config = parse_args(argv)
    if not config.join_key:
        logger.error("no join key: pass --key looma_... (get one from the admin page)")
        return 2
    try:
        agent = Agent(config)
    except BadJoinKey as exc:
        logger.error("%s", exc)
        return 2

    # SIGTERM is how `docker stop` and the launcher ask us to finish. Answering
    # it means the stream closes cleanly instead of the orchestrator waiting
    # for a keepalive to time out.
    def _shutdown(signum, _frame):
        logger.info("shutting down on signal %s", signum)
        agent.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _shutdown)
    return agent.run()


if __name__ == "__main__":
    raise SystemExit(main())
