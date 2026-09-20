"""Orchestrator entrypoint: one gRPC service, one HTTP app, one loop.

    python -m looma.orchestrator.server        (configuration via env, see config.py)

Everything a node says arrives on the stream it opened — commands, task input,
results, telemetry. Nothing dials a node, so no node needs an address, an open
port, or anything configured on its owner's router.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from pathlib import Path

import grpc
import uvicorn

from looma.api.app import create_app
from looma.logging_config import get_logger
from looma.orchestrator.agents import AgentHub, add_agent_gateway_to_server
from looma.orchestrator.config import OrchestratorConfig
from looma.orchestrator.keys import KeyStore
from looma.orchestrator.public_addr import resolve_public_address
from looma.accounts.store import Accounts
from looma.usage.deployments import Deployments
from looma.usage.training import TrainingJobs
from looma.usage.ledger import Ledger
from looma.orchestrator.tls import CertPaths, server_credentials
from looma.store.database import Database, DatabaseUnavailable, database_url
from looma.orchestrator.releases import ReleaseStore
from looma.orchestrator.rendezvous import RendezvousNode, host_of
from looma.orchestrator.state import StateStore

logger = get_logger(__name__)

# A result file or a model's input can be large; the default 4 MB would make
# anything real fail in a way that looks like a network problem.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


async def serve_grpc(*, hub: AgentHub, port: int,
                     certificates: Optional[CertPaths] = None) -> grpc.aio.Server:
    server = grpc.aio.server(options=[
        ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
        ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
        # The stream is long-lived and mostly silent, which is exactly the
        # traffic pattern NATs and tunnels drop without telling anyone. Without
        # keepalive a node sits reading a stream whose other end is long gone:
        # it never errors, so it never reconnects, and simply disappears.
        ("grpc.keepalive_time_ms", 20000),
        ("grpc.keepalive_timeout_ms", 10000),
        ("grpc.keepalive_permit_without_calls", 1),
        # And the server must not scold agents for pinging at that rate.
        ("grpc.http2.min_ping_interval_without_data_ms", 10000),
        ("grpc.http2.max_pings_without_data", 0),
    ])
    add_agent_gateway_to_server(server, hub)
    credentials = server_credentials(certificates or CertPaths())
    if credentials is None:
        # Открытым текстом — только когда сертификат не назван вовсе. Сказать
        # об этом вслух обязательно: по этому каналу едут секрет ключа и
        # команды запуска чужого кода, и «забыли настроить» не должно выглядеть
        # так же, как «настроили».
        server.add_insecure_port(f"[::]:{port}")
        logger.warning("AgentGateway слушает :%d БЕЗ шифрования — задайте "
                       "LOOMA_TLS_CERT и LOOMA_TLS_KEY", port)
    else:
        server.add_secure_port(f"[::]:{port}", credentials)
        logger.info("AgentGateway слушает :%d по TLS", port)
    await server.start()
    return server


RECONCILE_EVERY_S = 60.0


async def _reconcile_forever(ledger, hub) -> None:
    """Закрывать аренды, за которыми больше нет группы.

    Раз в минуту, а не по событию: события бывают потеряны — именно тогда, когда
    что-то пошло не так, то есть ровно в тех случаях, ради которых сверка и
    нужна.
    """
    while True:
        await asyncio.sleep(RECONCILE_EVERY_S)
        try:
            # Живая — та, у которой ещё есть незаконченные задачи. Запись о
            # группе переживает её саму (она нужна экранам), и если считать
            # живыми все записи, аренда законченного обучения тикала бы, пока
            # кто-нибудь не удалит группу руками.
            live = [gid for gid, rec in hub.groups.items() if not hub.group_finished(rec)]
            await ledger.reconcile(live)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("сверка журнала потребления не удалась")


#: Как часто сводить обучения без открытой вкладки: результат задачи на узле
#: живёт час, а адаптер надо забрать до того.
TRAINING_SWEEP_S = 30.0


async def _sweep_training_forever(app) -> None:
    while True:
        await asyncio.sleep(TRAINING_SWEEP_S)
        try:
            await app.state.sweep_training()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("сводка обучений не удалась")


#: Как часто пробовать очередь ожидания аренды. Чаще бессмысленно: узлы
#: освобождаются по секундам, а не по миллисекундам.
WAITING_SWEEP_S = 10.0


async def _serve_waiting_forever(app) -> None:
    while True:
        await asyncio.sleep(WAITING_SWEEP_S)
        try:
            await app.state.serve_waiting()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("проход очереди ожидания не удался")


async def run() -> None:
    config = OrchestratorConfig.from_env()
    Path(config.data_dir).mkdir(parents=True, exist_ok=True)

    certificates = CertPaths.from_env()

    # База поднимается до всего остального: без неё нет учётных записей, а
    # значит и кабинета. Работать без неё можно — тогда остаётся аварийный
    # админский токен, — но узнать об этом надо на старте, а не на первом входе.
    database, accounts, ledger, fleet, training = None, None, None, None, None
    if database_url():
        database = Database(database_url())
        try:
            await database.connect()
            applied = await database.migrate()
            if applied:
                logger.info("миграции накатаны: %s", ", ".join(applied))
            accounts = Accounts(database)
            ledger = Ledger(database)
            fleet = Deployments(database)
            training = TrainingJobs(database)
            if await accounts.count() == 0:
                logger.warning("в базе нет ни одной учётной записи — заведите "
                               "администратора: python -m looma.accounts.bootstrap <почта>")
        except DatabaseUnavailable as exc:
            # Не падаем: узлы и уже развёрнутые модели важнее кабинета, и
            # оркестратор без базы всё ещё умеет ими управлять.
            logger.error("%s; работаю без учётных записей", exc)
            database, accounts, ledger, fleet, training = None, None, None, None, None
    else:
        logger.warning("LOOMA_DATABASE_URL не задан — учётных записей нет, "
                       "работает только аварийный админский токен")

    public = resolve_public_address(config.grpc_port)
    logger.info("nodes will dial %s (%s)", public.address, public.source)
    if public.warning:
        logger.warning("%s", public.warning)

    # Ключ несёт не только адрес, но и то, как по нему звонить: узел с ключом
    # старого образца продолжит ходить открытым текстом, а с новым — только по
    # TLS. Без этого пришлось бы угадывать по виду адреса.
    keystore = KeyStore(public_address=public.address, path=config.keystore_path,
                        tls=certificates.configured)
    releases = ReleaseStore(Path(config.releases_dir))

    # The p2p entry point handed to every node at registration. One address is
    # all a node needs to reach every other: peers are found by id through a
    # shared entry point, and bootstrap addresses are fixed when a libp2p node
    # is built.
    rendezvous = RendezvousNode(public_host=host_of(public.address))
    rendezvous.start()

    # Where a node fetches an agent payload from: the same host it dials, on
    # the HTTP port. Empty when we do not know our own address, in which case
    # no release is offered rather than one nobody can download.
    dial_host = host_of(public.address)
    hub = AgentHub(
        keystore=keystore,
        rendezvous=rendezvous,
        releases=releases,
        release_base_url=f"http://{dial_host}:{config.http_port}" if dial_host else "",
        store=StateStore(config.state_path),
    )
    # До того, как узлы начнут дозваниваться: иначе первый доклад придёт в
    # пустой хаб, его задачи окажутся незнакомыми, и мы заведём их заново —
    # уже без имени модели и без группы, которые лежат в файле.
    hub.restore()

    grpc_server = await serve_grpc(hub=hub, port=config.grpc_port,
                                   certificates=certificates)

    # Сверка журнала с живыми группами. Группа умеет исчезнуть сама — узел
    # отвалился, задача упала, оркестратор перезапустился, — и без сверки такая
    # аренда тикала бы вечно, а счёт рос бы молча.
    watcher = None
    if ledger is not None:
        watcher = asyncio.create_task(_reconcile_forever(ledger, hub),
                                      name="usage-reconcile")
    app = create_app(agents=hub, releases=releases, keystore=keystore,
                     config=config, public_address=public, accounts=accounts,
                     ledger=ledger, deployments=fleet, training=training)
    # Перед оркестратором два nginx (edge и web) на этой же машине: схему
    # запроса и адрес клиента берём из X-Forwarded-*, но только когда
    # соединение пришло с 127.0.0.1 — с любого другого адреса заголовкам не
    # верим. От схемы зависит флаг Secure у сессионной cookie.
    http = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=config.http_port,
                                         log_level="info", loop="asyncio",
                                         proxy_headers=True,
                                         forwarded_allow_ips="127.0.0.1"))
    logger.info("HTTP on :%d  (dashboard at /admin)", config.http_port)
    flusher = asyncio.create_task(hub.flush_loop())
    sweeper = asyncio.create_task(_sweep_training_forever(app), name="training-sweep")
    usher = asyncio.create_task(_serve_waiting_forever(app), name="rent-waiting")
    try:
        await http.serve()
    finally:
        usher.cancel()
        sweeper.cancel()
        flusher.cancel()
        try:
            await flusher
        except asyncio.CancelledError:
            pass
        hub.flush()
        await grpc_server.stop(5)
        if watcher is not None:
            watcher.cancel()
        if database is not None:
            await database.close()
        rendezvous.stop()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("stopped")


if __name__ == "__main__":
    main()
