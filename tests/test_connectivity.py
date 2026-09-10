"""Кто с кем сойдётся, и что говорить, когда никто.

Правило топологическое, а не измеренное: узлы докладывают о себе, а не друг о
друге. Ошибка здесь выглядит не как ошибка — кластер собирается медленно или
не собирается вовсе, и причину искать пойдут в своём коде.
"""

from __future__ import annotations

from looma.orchestrator.connectivity import (can_meet, pairs_needing_relay,
                                            prefer_meshy, verdict)


def node(node_id: str, **kw) -> dict:
    base = {"node_id": node_id, "reachable": False, "symmetric_nat": False,
            "direct_share": 0.0, "vram_free_bytes": 0}
    base.update(kw)
    return base


# ---------------------------------------------------------------- встреча
def test_принимающий_входящие_сходится_с_кем_угодно():
    """Достаточно одного: второй просто дозвонится до него."""
    open_node = node("a", reachable=True)
    assert can_meet(open_node, node("b", symmetric_nat=True))
    assert can_meet(node("b", symmetric_nat=True), open_node)


def test_два_обычных_nat_рассчитывают_на_пробивание():
    assert can_meet(node("a"), node("b"))


def test_симметричный_nat_убивает_пробивание():
    """Такой NAT даёт каждому адресату свой порт: пробитое отверстие ведёт не
    туда, куда нужно."""
    assert not can_meet(node("a", symmetric_nat=True), node("b"))
    assert not can_meet(node("a", symmetric_nat=True), node("b", symmetric_nat=True))


# ------------------------------------------------------------- порядок
def test_связные_узлы_идут_первыми():
    nodes = [
        node("глухой", symmetric_nat=True, vram_free_bytes=99),
        node("обычный"),
        node("открытый", reachable=True),
    ]
    assert [n["node_id"] for n in prefer_meshy(nodes)] == [
        "открытый", "обычный", "глухой"]


def test_при_равной_связности_решает_память():
    nodes = [node("мало", vram_free_bytes=8), node("много", vram_free_bytes=64)]
    assert [n["node_id"] for n in prefer_meshy(nodes)][0] == "много"


def test_глухой_узел_не_выбрасывается_а_отодвигается():
    """Узел за симметричным NAT — всё ещё полезная машина."""
    nodes = [node("глухой", symmetric_nat=True), node("открытый", reachable=True)]
    assert len(prefer_meshy(nodes)) == 2


# ------------------------------------------------------------- решение
def test_связная_группа_идёт_напрямую():
    got = verdict([node("a", reachable=True), node("b")], relay_available=True)
    assert got == {"ok": True, "path": "direct", "relayed_pairs": [], "why": ""}


def test_несвязная_группа_с_реле_поедет_но_с_предупреждением():
    got = verdict([node("a", symmetric_nat=True), node("b", symmetric_nat=True)],
                  relay_available=True)
    assert got["ok"] and got["path"] == "relay"
    assert got["relayed_pairs"] == [("a", "b")]
    assert "медленнее" in got["why"]


def test_несвязная_группа_без_реле_отвергается_с_причиной():
    """Единственный случай, когда Ray действительно не поедет. Сейчас он
    молчал бы, и это выглядело бы зависанием."""
    got = verdict([node("a", symmetric_nat=True), node("b", symmetric_nat=True)],
                  relay_available=False)
    assert not got["ok"]
    assert "a↔b" in got["why"]
    assert "реле не развёрнуто" in got["why"]


def test_один_узел_всегда_сходится_сам_с_собой():
    assert verdict([node("a", symmetric_nat=True)], relay_available=False)["ok"]


def test_длинный_список_пар_сокращается():
    """Иначе сообщение об ошибке становится нечитаемым ровно там, где его
    читают внимательнее всего."""
    nodes = [node(f"n{i}", symmetric_nat=True) for i in range(6)]
    why = verdict(nodes, relay_available=False)["why"]
    assert "и ещё" in why
    assert len(pairs_needing_relay(nodes)) == 15


def test_узел_всегда_сходится_сам_с_собой():
    """Два ранга на одной машине разговаривают по настоящему локалхосту —
    сеть между ними не участвует вовсе. Единственная конфигурация, которой
    связность не нужна в принципе, не должна отвергаться из-за NAT."""
    глухой = node("nv3-a", symmetric_nat=True)
    assert pairs_needing_relay([глухой, глухой]) == []
    assert verdict([глухой, глухой], relay_available=False)["ok"]


def test_разные_узлы_за_симметричным_nat_всё_ещё_не_сходятся():
    """Оговорка про себя не должна отменять само правило."""
    a = node("nv3-a", symmetric_nat=True)
    b = node("nv3-b", symmetric_nat=True)
    assert pairs_needing_relay([a, b]) == [("nv3-a", "nv3-b")]


# ------------------------------------------- «в сети» отдельно от «доступен»
def test_состояние_сети_отдаётся_наружу():
    """Два разных вопроса, и путать их дорого.

    `reachable` — дозвонятся ли ДО узла. `in_network` — дозвонится ли он сам,
    то есть видит ли точку встречи, через которую ищутся адреса по peer id.
    Узел без DHT принимает входящие как ни в чём не бывало и молча не находит
    никого; пока это не было видно снаружи, разбирательство сводилось к чтению
    лога того, кто пытался позвонить.
    """
    from looma.orchestrator.agents import AgentNode

    вне = AgentNode(node_id="a", peer_id="p", reachable=True, in_network=False)
    assert вне.as_dict()["reachable"] is True
    assert вне.as_dict()["in_network"] is False

    внутри = AgentNode(node_id="b", peer_id="p", reachable=False, in_network=True)
    assert внутри.as_dict()["in_network"] is True


def test_по_умолчанию_узел_считается_вне_сети():
    """Пока узел не сказал обратного, обещать связь нельзя: старые агенты
    поля не шлют, и молчание не должно выглядеть как «всё хорошо»."""
    from looma.orchestrator.agents import AgentNode

    assert AgentNode(node_id="a").as_dict()["in_network"] is False


def test_местные_адреса_не_делают_узел_достижимым():
    """Регрессия из панели: после того как узлы начали объявлять свои
    локальные адреса ради соседей за одним роутером, проверка «есть адрес без
    /p2p-circuit» стала истинной для КАЖДОГО узла. Все разом сделались
    «принимает входящие», а связность перестала проверяться вовсе."""
    from looma.orchestrator.connectivity import reachable_from

    assert reachable_from(["/ip4/10.124.10.12/tcp/47100",
                           "/ip4/10.124.10.12/udp/47100/quic-v1"]) is False
    assert reachable_from(["/ip4/172.17.0.1/tcp/47100"]) is False
    assert reachable_from(["/ip4/95.79.46.1/tcp/47100"]) is True


def test_несвязная_пара_снова_видна():
    """То, ради чего эта проверка и существует: две машины, у которых наружу
    смотрит только реле, встретятся лишь пробиванием — и об этом надо знать
    до запуска, а не через пятнадцать минут отказов."""
    from looma.orchestrator.connectivity import pairs_needing_relay, reachable_from

    nv2 = {"node_id": "nv2", "symmetric_nat": True,
           "reachable": reachable_from(["/ip4/10.124.10.12/tcp/47100"])}
    nv3 = {"node_id": "nv3", "symmetric_nat": True,
           "reachable": reachable_from(["/ip4/10.124.10.11/tcp/47100"])}

    assert pairs_needing_relay([nv2, nv3]) == [("nv2", "nv3")]
