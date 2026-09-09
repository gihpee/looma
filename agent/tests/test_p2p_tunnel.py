"""Настоящее TCP-соединение между двумя узлами поверх p2p.

Здесь поднимаются две живые lattica-ноды и настоящий сервер на локалхосте
одной из них. Туннель, проверенный на моках, не проверяет ничего: вся его
сложность в том, как транспорт ведёт себя с байтами, а не в нашей логике.

Заодно тут меряется цена. Без числа объём работы над многоузловым Ray
оценить нельзя (docs/RAY.md).
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from looma_agent.p2p import PeerNode, lattica_available  # noqa: E402
from looma_agent.p2p.tunnel import RemoteSide, TunnelRefused, pump  # noqa: E402

pytestmark = pytest.mark.skipif(
    not lattica_available(), reason="без lattica проверять нечего")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Echo:
    """Локальная служба, до которой будет тянуться сосед."""

    def __init__(self) -> None:
        self.port = free_port()
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", self.port))
        self._server.listen(8)
        self._stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._echo, args=(client,), daemon=True).start()

    def _echo(self, client: socket.socket) -> None:
        try:
            while True:
                piece = client.recv(65536)
                if not piece:
                    return
                client.sendall(piece)
        except OSError:
            return
        finally:
            client.close()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._server.close()
        except OSError:
            pass


@pytest.fixture(scope="module")
def rendezvous(tmp_path_factory):
    """Точка встречи: соседи находят друг друга по id, не зная адресов."""
    port = free_port()
    node = PeerNode(port=port, key_dir=str(tmp_path_factory.mktemp("rdv")))
    identity = node.start(on_message=lambda msg: None)
    try:
        yield f"/ip4/127.0.0.1/tcp/{port}/p2p/{identity.peer_id}"
    finally:
        node.close()


@pytest.fixture(scope="module")
def pair(tmp_path_factory, rendezvous):
    """Две ноды: `a` тянется через туннель к службе, живущей рядом с `b`."""
    a = PeerNode(port=free_port(), key_dir=str(tmp_path_factory.mktemp("ta")),
                 bootstraps=[rendezvous])
    b = PeerNode(port=free_port(), key_dir=str(tmp_path_factory.mktemp("tb")),
                 bootstraps=[rendezvous])
    a.start(on_message=lambda m: None)
    id_b = b.start(on_message=lambda m: None)
    echo = Echo()
    # Сосед может открывать только то, что мы разрешили: иначе через туннель
    # достаётся любой порт этой машины, включая порты чужих задач.
    b.tunnels.allow = lambda port: port == echo.port
    time.sleep(2)
    try:
        yield a, id_b.peer_id, echo
    finally:
        echo.stop()
        a.close()
        b.close()


def side(a: PeerNode, peer_id: str, port: int) -> RemoteSide:
    return RemoteSide(a._stub_for(peer_id), uuid.uuid4().hex[:12], port)


def through(a, peer_id, port, payload: bytes, *, timeout: float = 60.0) -> bytes:
    """Прогнать байты туда и обратно через туннель, как это делал бы Ray."""
    remote = side(a, peer_id, port)
    remote.open()
    got = bytearray()
    done = threading.Event()

    def read() -> None:
        try:
            for piece in remote.read():
                got.extend(piece)
                if len(got) >= len(payload):
                    break
        finally:
            done.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    assert remote.write(payload), "сосед не принял данные"
    done.wait(timeout)
    remote.close()
    return bytes(got)


def test_байты_доходят_до_соседа_и_возвращаются(pair):
    a, peer_id, echo = pair
    assert through(a, peer_id, echo.port, b"looma") == b"looma"


def test_двоичные_данные_не_портятся(pair):
    """base64 в одну сторону, сырые байты в другую — оба конца обязаны
    пережить всё, включая нули и то, что не UTF-8."""
    a, peer_id, echo = pair
    payload = bytes(range(256)) * 8
    assert through(a, peer_id, echo.port, payload) == payload


def test_неразрешённый_порт_закрыт(pair):
    """Иначе сосед дотягивается до любого порта этой машины."""
    a, peer_id, echo = pair
    with pytest.raises(TunnelRefused, match="не открыт"):
        side(a, peer_id, echo.port + 1).open()


def test_закрытый_туннель_не_принимает_данные(pair):
    a, peer_id, echo = pair
    remote = side(a, peer_id, echo.port)
    remote.open()
    remote.close()
    assert remote.write("поздно".encode()) is False


@pytest.mark.slow
def test_сколько_это_стоит(pair):
    """Не проверка, а ЗАМЕР — то самое число, без которого нельзя оценить
    многоузловой Ray. Печатается, а не сравнивается с порогом: порог зависит
    от машины, а знание нужно от прогона."""
    a, peer_id, echo = pair

    started = time.time()
    assert through(a, peer_id, echo.port, b"x") == b"x"
    latency_ms = (time.time() - started) * 1000

    payload = b"y" * (4 * 1024 * 1024)
    started = time.time()
    got = through(a, peer_id, echo.port, payload, timeout=300)
    seconds = time.time() - started

    assert got == payload, f"вернулось {len(got)} из {len(payload)} байт"
    mbits = (len(payload) * 8 / 1e6) / seconds
    print(f"\n  туда-обратно на одном байте: {latency_ms:.0f} мс"
          f"\n  4 МБ туда-обратно:           {seconds:.1f} с"
          f"\n  сквозная полоса:             {mbits:.0f} Мбит/с")


# --------------------------------------------------------- проброс портов
class SameNumberElsewhere:
    """Сосед, у которого тот же номер порта ведёт к другой службе.

    Схема требует ОДНОГО номера на обеих сторонах: ранг M слушает порт P у
    себя, и все остальные узлы держат P у себя же, проксируя к нему. На разных
    машинах это и есть суть; на одной — невозможно, потому что номер занят
    один раз.

    Поэтому здесь подставлена ровно эта разница и ничего больше: сторона
    приёма настоящая (`Endpoint`), транспорт между сторонами прямой, а номер
    порта переводится так, как его перевела бы граница машин.
    """

    def __init__(self, endpoint, target: int) -> None:
        self.endpoint = endpoint
        self.target = target

    def tunnel_connect(self, message):
        return self.endpoint.connect(message["conn"], self.target)

    def tunnel_open(self, message):
        return self.endpoint.read(message["conn"])

    def tunnel_write(self, message):
        import base64 as _b64

        return self.endpoint.write(message["conn"], _b64.b64decode(message["data"]))

    def tunnel_close(self, message):
        return self.endpoint.close(message["conn"])


def test_чужой_порт_становится_местным(pair):
    """То, ради чего всё это: софт подключается к 127.0.0.1 и попадает на
    соседнюю машину, ничего про неё не зная."""
    from looma_agent.p2p.tunnel import Endpoint
    from looma_agent.tasks.forward import Forwarder

    _a, _peer_id, echo = pair
    endpoint = Endpoint(allow=lambda port: port == echo.port)
    local = free_port()
    forwarder = Forwarder(
        stub_for=lambda _peer: SameNumberElsewhere(endpoint, echo.port))
    try:
        opened = forwarder.open("t", mine=[], remote={1: "сосед"},
                                ports={1: [local]})
        assert opened["listening"] == 1

        # Обычное TCP-соединение на локалхост — никакого знания о соседе.
        with socket.create_connection(("127.0.0.1", local), timeout=30) as sock:
            sock.sendall(b"through the tunnel")
            got = b""
            while len(got) < 18:
                piece = sock.recv(4096)
                if not piece:
                    break
                got += piece
        assert got == b"through the tunnel"
    finally:
        forwarder.close_all()
        endpoint.close_all()


def test_порт_отпускается_вместе_с_задачей(pair):
    """Иначе слушатели держат чужие порты на машине владельца навсегда."""
    from looma_agent.tasks.forward import Forwarder

    a, peer_id, _echo = pair
    local = free_port()
    forwarder = Forwarder(stub_for=a.stub_for)
    forwarder.open("t", mine=[], remote={1: peer_id}, ports={1: [local]})
    assert forwarder.listening == 1
    forwarder.close("t")
    assert forwarder.listening == 0
    # Порт свободен: тот же номер можно занять заново.
    again = socket.socket()
    again.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    again.bind(("127.0.0.1", local))
    again.close()


def test_соседи_на_этой_же_машине_не_проксируются():
    """Их сервер уже слушает эти порты по-настоящему. Вклиниться значило бы
    не ускорить, а сломать: порт занят, слушатель не встанет."""
    from looma_agent.tasks.forward import Forwarder

    allowed = []
    forwarder = Forwarder(stub_for=None, allow_local=allowed.extend)
    opened = forwarder.open("t", mine=[20000, 20001], remote={}, ports={0: [20000]})
    assert opened["listening"] == 0
    assert allowed == [20000, 20001], "свои порты всё равно надо открыть входящим"


def test_без_p2p_проброс_отказывает_внятно():
    """Молча не слушать — значит оставить «кластер не собрался» без причины."""
    from looma_agent.tasks.forward import ForwardRefused, Forwarder

    forwarder = Forwarder(stub_for=None)
    with pytest.raises(ForwardRefused, match="прямого канала"):
        forwarder.open("t", mine=[], remote={1: "peer"}, ports={1: [free_port()]})


def test_отказ_туннеля_называет_что_знает_dht(caplog):
    """«Адреса нет» и «адрес есть, но не набрался» — разные поломки.

    В сообщении lattica они неразличимы: `Failed to reconnect to peer` в обоих
    случаях. Со стенда: на этом различии застрял весь разбор, потому что по
    логу нельзя было понять, искать причину на своей стороне или на чужой.
    """
    import logging

    from looma_agent.tasks.forward import Forwarder

    def упрямый(_peer):
        raise RuntimeError("RPC call failed")

    вперёд = Forwarder(stub_for=упрямый,
                       addresses_of=lambda _p: ["/ip4/203.0.113.7/tcp/47100"])
    with caplog.at_level(logging.WARNING):
        вперёд._carry(_закрытый_сокет(), "12D3KooWDee41w6D", 22600)
    сказано = caplog.text
    assert "203.0.113.7" in сказано, "адрес из DHT обязан попасть в сообщение"


def test_отказ_без_адресов_так_и_говорит(caplog):
    import logging

    from looma_agent.tasks.forward import Forwarder

    def упрямый(_peer):
        raise RuntimeError("RPC call failed")

    вперёд = Forwarder(stub_for=упрямый, addresses_of=lambda _p: [])
    with caplog.at_level(logging.WARNING):
        вперёд._carry(_закрытый_сокет(), "12D3KooWDee41w6D", 22600)
    assert "НИ ОДНОГО адреса" in caplog.text


def _закрытый_сокет():
    import socket

    один, другой = socket.socketpair()
    другой.close()
    return один


def test_слушаем_и_на_адресе_машины(pair):
    """Ray переписывает loopback в адрес машины — и стучится туда.

    Проверено на стенде: из `--address 127.0.0.1:65000` он делает
    `10.124.10.11:65000`, и флаг `--node-ip-address 127.0.0.1` этому не мешает.
    Спорить с этим нечем, поэтому слушать надо на обоих адресах: иначе
    присоединяющийся ранг стучится туда, где никого нет, и кластер не
    собирается — молча, до самого таймаута.
    """
    import socket as sock_mod

    from looma_agent.tasks.forward import Forwarder, own_address

    свой = own_address()
    if not свой:
        pytest.skip("у машины нет адреса, кроме локалхоста")

    a, peer_id, _echo = pair
    порт = free_port()
    forwarder = Forwarder(stub_for=a.stub_for)
    try:
        forwarder.open("t", mine=[], remote={1: peer_id}, ports={1: [порт]})
        for адрес in ("127.0.0.1", свой):
            проба = sock_mod.socket(); проба.settimeout(2)
            код = проба.connect_ex((адрес, порт)); проба.close()
            assert код == 0, f"на {адрес}:{порт} никто не слушает"
    finally:
        forwarder.close_all()


def test_счёт_идёт_по_портам_а_не_сокетам(pair):
    """На порт приходится два сокета; счёт по ним удваивал бы число."""
    from looma_agent.tasks.forward import Forwarder

    a, peer_id, _echo = pair
    forwarder = Forwarder(stub_for=a.stub_for)
    try:
        forwarder.open("t", mine=[], remote={1: peer_id},
                       ports={1: [free_port(), free_port()]})
        assert forwarder.listening == 2
    finally:
        forwarder.close_all()


def test_обрыв_туннеля_не_остаётся_без_следа(caplog, monkeypatch):
    """Со стенда: кластер Ray собирался, через несколько минут разваливался, и
    в логах при этом не было НИЧЕГО, а состояние показывалось как «ok».

    Связь raylet с головой держится долгим соединением через туннель. Его обрыв
    и есть развал кластера — значит он обязан быть событием, а не тишиной.
    """
    import logging

    from looma_agent.tasks import forward as fwd

    monkeypatch.setattr(fwd, "RemoteSide", lambda *a, **k: _Молчун())
    # Долгое соединение: короткие Ray открывает пачками, их в лог не берут.
    monkeypatch.setattr(fwd, "pump",
                        lambda *a, **k: "поток от соседа: connection reset")
    monkeypatch.setattr(fwd.time, "monotonic",
                        _по_очереди([100.0, 400.0]))

    вперёд = fwd.Forwarder(stub_for=lambda _p: object())
    with caplog.at_level(logging.INFO):
        вперёд._carry(_закрытый_сокет(), "12D3KooWDee41w6D", 22600)

    сказано = caplog.text
    assert "прожил 300 с" in сказано, "длительность обязана попасть в лог"
    assert "connection reset" in сказано, "и причина тоже"


class _Молчун:
    def open(self): return None
    def close(self): pass


def _по_очереди(values):
    остаток = list(values)
    def часы():
        return остаток.pop(0) if остаток else values[-1]
    return часы


def test_причина_обрыва_называется_словами():
    """Пустая причина бесполезна: «оборвалось» и так видно по факту."""
    import threading as th

    from looma_agent.p2p.tunnel import pump

    один, другой = socket.socketpair()

    class Немой:
        def write(self, _piece): return True
        def read(self): return iter(())
        def close(self): pass

    другой.close()                      # местная сторона исчезла
    why = pump(один, Немой(), closed=th.Event())
    assert why and why != "обе стороны замолчали", f"невнятная причина: {why!r}"


def test_называются_оба_направления():
    """«Ray закрыл соединение» и «сосед перестал отвечать» — разные диагнозы.

    Потоки кончаются почти одновременно, и запись только первого делала второй
    невидимым: побеждал тот, кто успел, а не тот, кто важнее. Со стенда именно
    на этом застряло объяснение развала кластера.
    """
    import threading as th

    from looma_agent.p2p.tunnel import pump

    один, другой = socket.socketpair()
    другой.close()                      # местная сторона исчезла

    class Немой:
        def write(self, _piece): return False    # и сосед не принимает
        def read(self): return iter(())
        def close(self): pass

    why = pump(один, Немой(), closed=th.Event())
    assert "наружу:" in why and "внутрь:" in why, f"названо одно направление: {why!r}"


def test_запасной_локалхост_когда_слушают_не_там():
    """Со стенда: кластер собрался и работал, а `looma-connect` получал
    «127.0.0.2:25607 не отвечает». Клиентский вход Ray поднялся не на адресе
    ранга, а на обычном локалхосте — и гадать, где именно софт задачи поставит
    слушателя, бесполезно: это зависит от его версии."""
    import socket
    import threading

    from looma_agent.p2p.tunnel import Endpoint

    сервер = socket.socket()
    сервер.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    сервер.bind(("127.0.0.1", 0))
    сервер.listen(1)
    порт = сервер.getsockname()[1]
    threading.Thread(target=lambda: сервер.accept(), daemon=True).start()

    # Задача назвала адрес ранга, а слушают на локалхосте.
    endpoint = Endpoint(allow=lambda p: p == порт,
                        host_for=lambda _p: "127.0.0.9")
    try:
        assert endpoint.connect("c1", порт)["ok"], "запасной адрес не пробовали"
    finally:
        endpoint.close("c1")
        сервер.close()


def test_отказ_называет_все_адреса():
    """Их три — адрес ранга, локалхост и адрес машины. Назвать один значит
    отправить искать не там: «не отвечает 192.168.1.5» читается как проблема
    сети, хотя проверялись ещё два локальных."""
    from looma_agent.p2p.tunnel import Endpoint

    endpoint = Endpoint(allow=lambda _p: True, host_for=lambda _p: "127.0.0.9")
    ответ = endpoint.connect("c2", 1)

    assert not ответ["ok"]
    assert "порт 1" in ответ["error"]
    assert "127.0.0.9" in ответ["error"] and "127.0.0.1" in ответ["error"]


def test_запасные_адреса_укладываются_в_срок_ray():
    """Ray ждёт соединения пять секунд и потом объявляет узел недоступным.

    Со стенда: кластер nv3+gtx, который собирался неделями, перестал вставать
    после того, как в дозвон добавился третий кандидат — адрес машины. На нём
    никто не слушает, и отказ оттуда не приходит вовсе, только таймаут; он
    съедал весь срок Ray, и тот сдавался, ни разу не попав в окно, когда голова
    уже готова. Раньше отказ был мгновенным, и Ray переоткрывал соединение
    сотни раз за минуту.
    """
    from looma_agent.p2p.tunnel import CONNECT_TIMEOUT_S, SPARE_TIMEOUT_S

    # Первому — полный срок, запасным — короткий: их двое, и вместе они не
    # должны съедать то, что осталось.
    assert SPARE_TIMEOUT_S * 2 < CONNECT_TIMEOUT_S / 2, (
        "запасные адреса съедают срок, отведённый на само соединение")


def test_отказ_на_закрытом_порту_мгновенный():
    """Именно на этом держится повтор у Ray: он переоткрывает соединение, пока
    голова поднимается, и каждый отказ должен стоить почти ничего."""
    import time

    from looma_agent.p2p.tunnel import Endpoint

    endpoint = Endpoint(allow=lambda _p: True, host_for=lambda _p: "127.0.0.1")
    начали = time.monotonic()
    ответ = endpoint.connect("c3", 1)
    ушло = time.monotonic() - начали

    assert not ответ["ok"]
    assert ушло < 2.0, f"отказ занял {ушло:.1f}с — Ray столько не ждёт"
