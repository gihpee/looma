"""This node's direct path to its neighbours.

Brought up when the rendezvous becomes known, which is NOT process start: the
address to bootstrap against arrives in the registration ack. So the link table
exists from the beginning and relays everything, and the p2p node is attached
to it later — everything downstream holds the same object and is never rewired.

Nothing here is fatal. A node that cannot join the peer network is slower, not
broken, and dropping a working GPU out of the pool over a networking nicety
would be the wrong trade.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import replace
import time
from typing import Callable, List, Optional

from looma_agent.p2p.links import LinkTable
from looma_agent.p2p.peer import (
    DEFAULT_P2P_PORT,
    PeerNode,
    behind_container_nat,
    dialable_from_outside,
    lattica_available,
)
from looma_agent.proto import agent_pb2

logger = logging.getLogger("looma_agent.p2p")

# Сколько ждать, прежде чем судить о достижимости узла. AutoNAT высказывается не
# мгновенно, а резервация на реле — это обмен с ним по сети. Раньше приговор
# выносился через доли секунды после старта и не отзывался никогда.
REACHABILITY_DELAY_S = float(os.environ.get("LOOMA_REACHABILITY_DELAY_S", "10"))

# The numbers this samples change on the scale of a network path settling, not
# of a token. Slow on purpose.
SAMPLE_INTERVAL_S = 15.0


def _env_relays() -> List[str]:
    return [a.strip() for a in os.environ.get("LOOMA_P2P_RELAY", "").split(",") if a.strip()]


def _enabled() -> bool:
    return os.environ.get("LOOMA_P2P", "1").strip().lower() not in ("0", "false", "no")


class PeerLayer:
    def __init__(
        self,
        *,
        on_message: Optional[Callable[[bytes], None]] = None,
        port: Optional[int] = None,
        key_dir: str = "",
    ) -> None:
        self.links = LinkTable()
        self.node: Optional[PeerNode] = None
        self.identity = None
        # Delivery of an inbound direct message. None until transport lands
        # (docs/AGENT_PLAN.md phase 7): a node with nothing to deliver to still
        # benefits from joining, because its neighbours can then reach IT.
        self._on_message = on_message or (lambda _payload: None)
        # Everything the heartbeat reports about reachability, sampled on the
        # sampler thread and read from here. Never measured in the heartbeat:
        # asking the p2p stack means entering its runtime, and a heartbeat that
        # does that stops arriving the moment the runtime is busy.
        self._visible: List[str] = []
        # This node's own distance to the relay. Measured directly rather than
        # read off the link table, because neighbours need it BEFORE any link
        # exists — a zero there means they cannot judge their side of the path.
        self._relay_rtt_ms: float = 0.0
        # Тоже сюда, и по той же причине, что и всё выше. Здесь это стоило
        # дорого: in_network() спрашивался ПРЯМО в ударе сердца, и когда
        # рантайм p2p был занят (кластер Ray, десятки туннелей на 93
        # проброшенных порта), вызов не возвращался. Со стенда, узел nv3:
        # контейнер up, процесс жив, ядро ни при чём, драйвер отвечает — а
        # наверх не уходит ничего.
        self._in_network: bool = False
        # Explicit so several agents can run in one process during tests: two
        # nodes sharing a key directory interfere, and a closed node does not
        # give its port back instantly.
        self._port = port
        self._key_dir = key_dir
        self._sampler: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ setup
    def on_rendezvous(self, addrs: List[str], relays: Optional[List[str]] = None) -> None:
        """Адреса точки встречи из ответа на регистрацию.

        Узел поднимается ОДИН раз за жизнь процесса, и это не упущение.
        Переподключением к точке встречи занимается сама lattica: адреса
        бутстрапа у неё уже есть, и она возвращается туда сама.

        Здесь стояла «вторая попытка»: при перерегистрации узел, потерявший
        связь с точкой встречи, закрывался и собирался заново. Она чинила
        симптом, которого к тому моменту уже не было (предупреждение о
        недостижимой точке встречи вызывал невозможный адрес /ip4/<домен>, и
        это исправлено в оркестраторе) — а вносила вот что:

        перезапуск оркестратора роняет точку встречи; агент перерегистрируется
        через три секунды, когда она ещё поднимается; «вторая попытка» видит
        отсутствие связи, ЗАКРЫВАЕТ исправный узел вместе с резервацией на реле
        и всеми соединениями и собирается заново — против точки встречи,
        которой ещё нет. Дальше попыток не будет: поток уже стабилен.

        Разрушать работающее ради задачи, которую решает нижний слой, нельзя.
        """
        if self.node is not None or not _enabled():
            return
        addrs = [a for a in (addrs or []) if a.strip()]
        if not addrs:
            logger.info("the orchestrator offers no rendezvous; messages go through it")
            return
        if not lattica_available():
            logger.info(
                "no p2p stack installed; messages go through the orchestrator. "
                "Install the extra to let this node talk to its neighbours "
                "directly: pip install 'looma-agent[p2p]'"
            )
            return
        self._bring_up(addrs, relays)

    def _bring_up(self, addrs: List[str], relays: Optional[List[str]]) -> None:
        # The rendezvous is a DHT entry point and nothing else. Pointing the
        # relay client at it leaves the node waiting for a reservation that
        # never comes (measured: 15s, zero peers). A real circuit-relay server
        # is a separate process; see relay/relay.mjs.
        relay_addrs = [a for a in (relays or []) if a.strip()] or _env_relays()
        options = {"bootstraps": addrs, "relay_servers": relay_addrs}
        if self._port is not None:
            options["port"] = self._port
        if self._key_dir:
            options["key_dir"] = self._key_dir
        node = PeerNode(**options)
        try:
            self.identity = node.start(on_message=self._on_message)
        except Exception:
            logger.warning("the p2p node did not start; relaying everything", exc_info=True)
            return
        self.node = node
        self._start_sampler()
        self.links.attach(
            send_direct=node.send_nowait,
            dial=node.warm,
            rtt=node.rtt_ms,
            relay_rtt=node.relay_rtt_ms,
        )
        # Не сразу: адреса узла и резервация на реле приходят не в тот же миг,
        # что и старт. Со стенда — предупреждение «реле не дало резервации»
        # печаталось через доли секунды после подъёма узла, когда обмен с реле
        # ещё физически не мог состояться, и больше никогда не отзывалось. В
        # логе оно выглядело поломкой при исправном реле.
        threading.Timer(REACHABILITY_DELAY_S, self._report_reachability,
                        args=(relay_addrs,)).start()

    def _report_reachability(self, relay_addrs: List[str]) -> None:
        """Say plainly which of the several silent failures this node is in.

        Вызывается с задержкой (REACHABILITY_DELAY_S) и берёт адреса ЗАНОВО, а
        не те, что были при старте: к этому времени AutoNAT успевает высказаться,
        а реле — выдать резервацию.

        They look identical from the outside — node up, peer id reported,
        nobody can reach it — and have nothing in common. Naming which one it
        is saves the whole investigation.
        """
        if self.node is None:
            return          # узел успели закрыть, пока мы ждали
        identity = self.identity
        if identity is None:
            return
        # Свежие, а не запомненные при старте: ради этого всё и откладывалось.
        try:
            видно = self.node.visible_addrs()
        except Exception:
            видно = list(identity.visible_addrs)
        if видно:
            identity = replace(identity, visible_addrs=видно)
        if identity.symmetric_nat:
            logger.warning(
                "this node is behind a symmetric NAT: peers cannot open a direct "
                "link to it and will relay"
            )
            return
        # Публичные, а не «все нециркуитные»: узел объявляет ещё и свои
        # локальные адреса ради соседей за тем же роутером, и по ним снаружи
        # не дозвониться. Считать их достижимостью — значит сказать «принимаю
        # входящие» про каждый узел без исключения.
        public = [a for a in identity.visible_addrs if dialable_from_outside(a)]
        if public:
            return                      # дозваниваются напрямую, говорить не о чем
        relayed = [a for a in identity.visible_addrs if "/p2p-circuit" in a]
        if relayed:
            # A reservation is held: nothing can dial this node directly, but
            # peers reach it through the relay and can try to punch through
            # from there. This is the state the relay exists to produce.
            logger.info("this node is reachable through the relay: %s", relayed[0])
            return
        if identity.visible_addrs:
            # Адреса есть, но все — местные. Снаружи узел не виден, и реле
            # резервации ему не дало: он не найдёт соседей и его не найдут.
            logger.warning(
                "this node shows only local addresses (%s): peers outside its "
                "own network cannot reach it, and the relay gave it no "
                "reservation", ", ".join(identity.visible_addrs[:3]))
            return
        port = self.node.port if self.node else DEFAULT_P2P_PORT
        if behind_container_nat():
            logger.warning(
                "this agent runs on a Docker bridge network, so no peer can ever "
                "open a direct link to it: the container's port is not the host's, "
                "and outgoing packets are translated again on the way out. Hole "
                "punching cannot work from here. Restart with --network host "
                "(and open port %d) to make the direct path possible",
                port,
            )
        elif relay_addrs:
            logger.warning(
                "no address of this node is reachable from outside, and the relay "
                "at %s gave it no reservation either. Check that the relay is "
                "running and its port is open from here",
                relay_addrs[0],
            )
        else:
            logger.warning(
                "no address of this node is reachable from outside (AutoNAT "
                "confirmed none) and no relay was offered. Peers will relay TO it "
                "through the orchestrator; it can still send directly. Forward "
                "port %d (TCP and UDP), or run a relay (docs/P2P_RELAY.md)",
                port,
            )

    def close(self) -> None:
        """Закрыть узел p2p и отпустить его порт.

        Со стенда: агента остановили, подняли снова — и он сообщил, что порт
        47100 занят, взяв 47101. Дальше номер рос с каждым перезапуском, а
        соседи продолжали искать узел там, где его больше нет.

        Держал порт прежний процесс: узел никогда не закрывался, и его
        освобождение зависело от того, как быстро система разберёт умерший
        процесс. Здесь это делается явно и до выхода.
        """
        node, self.node = self.node, None
        if node is None:
            return
        try:
            node.close()
        except Exception:
            logger.debug("p2p node did not close cleanly", exc_info=True)
        else:
            logger.info("p2p node closed, port %d released", node.port)

    def _start_sampler(self) -> None:
        if self._sampler is not None and self._sampler.is_alive():
            return          # пересборка узла не повод заводить вторую такую же

        def sample() -> None:
            while self.node is not None:
                try:
                    self._sample_once()
                except Exception:
                    logger.debug("sampling the p2p state failed", exc_info=True)
                time.sleep(SAMPLE_INTERVAL_S)

        self._sampler = threading.Thread(target=sample, name="looma-p2p-sampler",
                                         daemon=True)
        self._sampler.start()

    def _sample_once(self) -> None:
        """Один проход опроса: всё, что телеметрия потом только читает.

        Отдельным методом, а не телом цикла, по двум причинам. Его можно
        позвать в тесте, не заводя поток. И он один: список того, что
        спрашивается у рантайма, не должен расходиться по коду — именно так
        `in_network()` и оказался когда-то прямо в status(), то есть в ударе
        сердца.
        """
        self._visible = self.node.visible_addrs()
        self._relay_rtt_ms = self.node.relay_rtt_ms() or 0.0
        self._in_network = bool(self.node.in_network())
        # A circuit address is not reachability: it means "through the relay",
        # which is the relay path under another name.
        # Не «есть нециркуитный адрес»: с тех пор как узел объявляет свои
        # локальные адреса ради соседей за тем же роутером, под это подходит
        # каждый узел. Достижим тот, до кого можно дозвониться СНАРУЖИ.
        self.links.set_self_reachable(
            any(dialable_from_outside(a) for a in self._visible)
        )
        self.links.refresh()

    # ----------------------------------------------------------------- report
    def warm(self, peer_id: str) -> bool:
        """Заранее навести маршрут к соседу. Ложь — не получилось, и это не беда.

        Зачем это здесь. Соединение к узлу за NAT сначала поднимается ЧЕРЕЗ
        реле, и только потом DCUtR пробивает его в прямое. Пробивание занимает
        секунды, а байтовый туннель поверх реле lattica открывать отказывается
        вовсе: «Only relayed connection available for peer». То есть первый же
        запрос к непрогретому соседу получает отказ, а не задержку.

        Конвейеру это давно известно — там маршруты наводятся в
        `LinkTable.set_neighbours`, до первого токена. Ray жил без этого, и
        отсюда кластеры, которые не собирались с узлом за NAT: Ray даёт на
        соединение пять секунд, а пробивание в них не укладывается.
        """
        node = self.node
        if node is None:
            return False
        try:
            return bool(node.warm(peer_id))
        except Exception:
            logger.debug("не удалось навести маршрут к %s", peer_id[:12], exc_info=True)
            return False

    def status(self) -> agent_pb2.PeerStatus:
        """What this node reports about its p2p state on every heartbeat.

        Reachability is re-read rather than taken from the identity captured at
        startup: it is not a constant. A relay reservation can arrive a second
        after the node joins, and AutoNAT needs a few probes before it confirms
        anything. Reporting the startup snapshot forever showed nodes as
        unreachable long after they had stopped being so.

        Counters are reported even with no identity at all: a node with no p2p
        stack relays everything, and that is exactly what the orchestrator
        needs to see.
        """
        stats = self.links.snapshot()
        identity = self.identity
        return agent_pb2.PeerStatus(
            peer_id=identity.peer_id if identity else "",
            symmetric_nat=bool(identity.symmetric_nat) if identity else False,
            direct=stats["direct"],
            relayed=stats["relay"],
            fallbacks=stats["fallbacks"],
            direct_share=stats["direct_share"],
            visible_addrs=self._visible or (identity.visible_addrs if identity else []),
            link_rtt_ms=stats["link_rtt_ms"],
            relay_rtt_ms=self._relay_rtt_ms or stats["relay_rtt_ms"],
            # Снимок с потока опроса, а не вопрос рантайму отсюда. Связь с
            # точкой встречи действительно теряется при её перезапуске, и
            # значение со старта было бы бесполезно — но обновляет его тот же
            # поток, что и остальное: этот метод зовёт удар сердца, и всё, что
            # в нём уходит в чужой рантайм, рано или поздно его останавливает.
            in_network=bool(self.node) and self._in_network,
        )

    def identity_message(self):
        """How peers can reach this node, for the registration. Empty until up."""
        identity = self.identity
        if identity is None:
            return None
        return identity
