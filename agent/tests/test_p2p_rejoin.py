"""Второй шанс войти в сеть.

Со стенда: агент поднялся в момент, когда точка встречи была недоступна, не
вошёл в сеть — и остался вне неё на сутки. Управляющий поток за это время
переподключался трижды, но p2p-узел создаётся один раз за жизнь процесса, и
`on_rendezvous` при повторной регистрации молча выходил. Чинилось это релизом
на весь парк вместо одной перерегистрации.
"""

from __future__ import annotations

from looma_agent.p2p import layer


class ПоддельныйУзел:
    """Столько от PeerNode, сколько трогает слой."""

    def __init__(self, peers=()) -> None:
        self.peers = list(peers)
        self.closed = False

    def start(self, on_message=None):
        from looma_agent.p2p.peer import PeerIdentity

        return PeerIdentity(peer_id="я")

    def connected_peers(self):
        return list(self.peers)

    def in_network(self):
        return bool(self.peers)

    def close(self):
        self.closed = True

    # то, что слой вешает на таблицу связей
    def send_nowait(self, *a, **kw): return True
    def warm(self, *a, **kw): return True
    def rtt_ms(self, *a, **kw): return 0.0
    def relay_rtt_ms(self, *a, **kw): return 0.0
    def visible_addrs(self): return []


def слой(monkeypatch, узел=None, собран=None):
    """Слой с подставленной сборкой узла: настоящий Lattica здесь не нужен."""
    сделан = {}

    def построить(**options):
        сделан["options"] = options
        новый = собран if собран is not None else ПоддельныйУзел(["сосед"])
        return новый

    made = layer.PeerLayer()
    monkeypatch.setattr(layer, "lattica_available", lambda: True)
    monkeypatch.setattr(layer, "_enabled", lambda: True)
    monkeypatch.setattr(layer, "PeerNode", построить)
    monkeypatch.setattr(type(made), "_report_reachability", lambda self, _a: None)
    monkeypatch.setattr(type(made), "_start_sampler", lambda self: None)
    if узел is not None:
        made.node = узел
    return made, сделан


def test_живой_узел_не_пересобирается_никогда(monkeypatch):
    """Ради этого правило и вернули.

    Перезапуск оркестратора роняет точку встречи; агент перерегистрируется
    через три секунды, когда она ещё поднимается. Прежняя «вторая попытка»
    видела отсутствие связи, ЗАКРЫВАЛА исправный узел вместе с резервацией на
    реле и собирала новый — против точки встречи, которой ещё нет. Дальше
    попыток не было: управляющий поток к тому моменту уже стабилен.

    Переподключением занимается сама lattica, и разрушать ради этого
    работающее нельзя.
    """
    одинокий = ПоддельныйУзел([])          # связи с точкой встречи нет
    слой_, сделан = слой(monkeypatch, узел=одинокий)

    слой_.on_rendezvous(["/dns4/looma.example/tcp/47100/p2p/aaa"], [])

    assert not одинокий.closed, "закрывать живой узел нельзя ни при каких условиях"
    assert слой_.node is одинокий
    assert "options" not in сделан, "и собирать новый тоже"


def test_повторная_регистрация_ничего_не_трогает(monkeypatch):
    """Она случается постоянно — при каждом обрыве управляющего потока."""
    живой = ПоддельныйУзел(["точка"])
    слой_, сделан = слой(monkeypatch, узел=живой)

    for _ in range(5):
        слой_.on_rendezvous(["/dns4/looma.example/tcp/47100/p2p/aaa"], [])

    assert not живой.closed
    assert слой_.node is живой
    assert "options" not in сделан


# ------------------------------------------------ «в сети» значит «с точкой встречи»
def сеть(bootstraps, connected):
    """PeerNode без Lattica: проверяется только правило, а не стек."""
    from looma_agent.p2p.peer import PeerNode

    node = PeerNode.__new__(PeerNode)
    node.bootstraps = list(bootstraps)
    node.connected_peers = lambda: list(connected)
    return node


def test_одно_реле_не_считается_сетью():
    """Со стенда, и это стоило дня.

    За резервацией узел идёт к реле первым делом, поэтому соединение с ним
    есть почти всегда. Считая его входом в сеть, узел без точки встречи
    выглядит здоровым — предупреждения при старте нет, — а соседей найти не
    может: адрес по peer id ищется через DHT, вход в который даёт именно
    точка встречи.
    """
    узел = сеть(["/dns4/looma.example/tcp/47100/p2p/ТОЧКА"], connected=["РЕЛЕ"])
    assert not узел.in_network()


def test_связь_с_точкой_встречи_и_есть_сеть():
    узел = сеть(["/dns4/looma.example/tcp/47100/p2p/ТОЧКА"],
                connected=["РЕЛЕ", "ТОЧКА"])
    assert узел.in_network()


def test_без_точки_встречи_вопрос_не_стоит():
    """Некуда входить — значит и жаловаться не на что."""
    assert сеть([], connected=[]).in_network()


def test_адрес_без_идентификатора_не_ломает_проверку():
    """Такой адрес набрать нельзя, но и отказывать из-за него нельзя."""
    assert сеть(["/ip4/1.2.3.4/tcp/47100"], connected=[]).in_network()


# ---------------------------------------------- то же самое видно снаружи
def test_состояние_сети_уходит_в_телеметрию(monkeypatch):
    """Без этого поля вопрос «состоит ли узел в DHT» не имел ответа нигде.

    Значок «принимает» отвечает на обратный вопрос — дозвонятся ли ДО него, —
    а предупреждение в логе видно только тому, у кого есть доступ к машине.
    Разбирательство сводилось к чтению лога того, кто пытался позвонить.
    """
    слой_, _ = слой(monkeypatch, узел=ПоддельныйУзел(["ТОЧКА"]))
    assert слой_.status().in_network is True

    слой_, _ = слой(monkeypatch, узел=ПоддельныйУзел([]))
    assert слой_.status().in_network is False


def test_без_p2p_узла_состояние_ложно(monkeypatch):
    """Узла нет — значит и в сети его нет; врать положительным ответом нельзя."""
    слой_, _ = слой(monkeypatch)
    слой_.node = None
    assert слой_.status().in_network is False


def test_узел_отпускает_порт_при_остановке():
    """Со стенда: агента остановили, подняли снова — и он сообщил, что порт
    47100 занят, взяв 47101. С каждым перезапуском номер рос, а соседи
    продолжали искать узел по прежнему. Держал порт прежний процесс: узел не
    закрывался никогда, и освобождение зависело от того, как быстро система
    разберёт умерший процесс."""
    from looma_agent.p2p.layer import PeerLayer

    закрыт = []

    class Узел:
        port = 47100

        def close(self):
            закрыт.append(True)

    layer = PeerLayer(on_message=lambda _m: None)
    layer.node = Узел()

    layer.close()

    assert закрыт == [True]
    assert layer.node is None, "ссылка на закрытый узел осталась"


def test_повторное_закрытие_безобидно():
    """Остановка приходит и по сигналу, и по кнопке из панели; закрыть дважды
    не должно быть ошибкой."""
    from looma_agent.p2p.layer import PeerLayer

    layer = PeerLayer(on_message=lambda _m: None)
    layer.close()
    layer.close()


def test_приговор_о_достижимости_выносится_не_сразу():
    """AutoNAT высказывается не мгновенно, а резервация на реле — это обмен с
    ним по сети. Со стенда: «реле не дало резервации» печаталось через доли
    секунды после старта, когда обмен ещё физически не мог состояться, и
    никогда не отзывалось — в логе это выглядело поломкой при исправном реле."""
    from looma_agent.p2p import layer as layer_mod

    assert layer_mod.REACHABILITY_DELAY_S >= 5, (
        "приговор выносится раньше, чем узел успевает договориться с реле")
