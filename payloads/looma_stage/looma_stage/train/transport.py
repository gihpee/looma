"""Как голова говорит со стадиями.

Одна и та же голова ведёт обучение на одном узле и на десяти: разница —
только в том, как до стадии доходит сообщение. Это и вынесено сюда.

    LocalTransport   стадии в этом же процессе; тензоры всё равно
                     проходят через провод (`wire`): граница между
                     стадиями обязана быть настоящей — с копией и без
                     общего графа, — иначе тест на одном процессе
                     пропустил бы то, что сломается на двух.

Сетевая доставка (через канал агента по рангу) — тот же набор сообщений,
но с ответом позже; она добавляется отдельно, и голова о ней ничего не
узнает.

Сообщения между стадиями (все — словари, тензоры упакованы `batch_wire`):

    train_forward   batch_id, micro, attention_mask, position_ids,
                    input_ids | tensors{hidden}, labels?, loss_scale?
    train_backward  batch_id, micro, tensors{grad}
    train_norm      → квадрат нормы градиента стадии
    train_step      lr, grad_scale
    train_save      dir
    train_collect   → кусок адаптера стадии (тензоры под глобальными именами)
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from looma_stage import batch_wire

logger = logging.getLogger("looma_stage.train.transport")


class Transport:
    """Что голове нужно уметь от стадий. Роли: 0 — первая, N-1 — последняя."""

    size: int

    def forward(self, stage: int, message: dict) -> dict:
        raise NotImplementedError

    def backward(self, stage: int, message: dict) -> dict:
        raise NotImplementedError

    def norms(self) -> List[float]:
        raise NotImplementedError

    def step(self, *, lr: float, grad_scale: float) -> None:
        raise NotImplementedError

    def save(self, directory: str) -> None:
        raise NotImplementedError

    def collect(self) -> List[Dict[str, object]]:
        raise NotImplementedError


# --------------------------------------------------------------- упаковка
def pack(torch, tensors: Dict[str, object]) -> dict:
    return batch_wire.pack_tensors(torch, tensors)


def unpack(torch, payload: Optional[dict]) -> Dict[str, object]:
    # Пустая карта — законна: первая стадия тензоров не получает.
    return batch_wire.unpack_tensors(torch, payload) if payload else {}


def handle(stage, message: dict) -> dict:
    """Одно сообщение — на объекте `TrainStage`. Общее для локальной и
    сетевой доставки: сеть только возит словари."""
    import torch

    kind = message.get("kind")
    if kind == "train_forward":
        tensors = unpack(torch, message.get("tensors"))
        answer = stage.forward(
            str(message["batch_id"]), int(message["micro"]),
            attention_mask=message["attention_mask"],
            position_ids=message["position_ids"],
            input_ids=message.get("input_ids"),
            hidden=tensors.get("hidden"),
            labels=message.get("labels"),
            loss_scale=float(message.get("loss_scale") or 1.0))
        return _pack_answer(torch, answer)
    if kind == "train_backward":
        grad = unpack(torch, message.get("tensors")).get("grad")
        return _pack_answer(torch, stage.backward(
            str(message["batch_id"]), int(message["micro"]), grad))
    if kind == "train_ping":
        return {"ok": True, "in_flight": stage.in_flight()}
    if kind == "train_norm":
        return {"norm_squared": stage.grad_norm_squared()}
    if kind == "train_step":
        return stage.step(lr=message.get("lr"),
                          grad_scale=float(message.get("grad_scale") or 1.0))
    if kind == "train_save":
        stage.save(message["dir"])
        return {"saved": str(message["dir"])}
    if kind == "train_collect":
        return {"tensors": pack(torch, stage.adapter_piece())}
    raise ValueError(f"стадии обучения незнакомо сообщение {kind!r}")


def _pack_answer(torch, answer: dict) -> dict:
    tensors = {name: answer.pop(name) for name in ("hidden", "grad") if name in answer}
    if tensors:
        answer["tensors"] = pack(torch, tensors)
    return answer


# --------------------------------------------------------------- локально
class LocalTransport(Transport):
    """Все стадии в этом процессе. Для одного узла — рабочий режим, для
    теста — способ проверить конвейер без сети."""

    def __init__(self, stages: List) -> None:
        if not stages:
            raise ValueError("нужна хотя бы одна стадия")
        self.stages = list(stages)
        self.size = len(self.stages)

    def _call(self, stage: int, message: dict) -> dict:
        # `handle` пакует тензоры ответа в провод, голова распаковывает —
        # словарь ушёл, словарь пришёл, и никакой тензор не разделён между
        # стадиями даже в одном процессе.
        return handle(self.stages[stage], message)

    def forward(self, stage: int, message: dict) -> dict:
        return self._call(stage, {**message, "kind": "train_forward"})

    def backward(self, stage: int, message: dict) -> dict:
        return self._call(stage, {**message, "kind": "train_backward"})

    def norms(self) -> List[float]:
        return [float(self._call(i, {"kind": "train_norm"})["norm_squared"])
                for i in range(self.size)]

    def step(self, *, lr: float, grad_scale: float) -> None:
        for i in range(self.size):
            self._call(i, {"kind": "train_step", "lr": lr, "grad_scale": grad_scale})

    def save(self, directory: str) -> None:
        for i in range(self.size):
            self._call(i, {"kind": "train_save", "dir": f"{directory}/stage-{i}"})

    def collect(self) -> List[Dict[str, object]]:
        import torch

        return [unpack(torch, self._call(i, {"kind": "train_collect"})["tensors"])
                for i in range(self.size)]


# ----------------------------------------------------------------- по сети
class ChannelTransport(Transport):
    """Стадия 0 — здесь, в процессе; остальные — за каналом агента.

    Голова шлёт стадии сообщение с `call_id` и ждёт ответ с тем же `call_id`
    — его стадия возвращает сообщением `train_reply` на ранг 0. Ожидание
    синхронное: GPipe и так делает шаги по очереди, а истинная конвейерная
    загрузка (несколько микробатчей в полёте) — следующая ступень.

    `send(message)` — то, чем стадия пишет соседу по рангу (`server.relay`):
    `target_stage` в сообщении говорит, кому. Ответы приходят снаружи через
    `deliver(message)` с потока приёма сообщений.
    """

    def __init__(self, local, *, size: int, send, timeout_s: float = 600.0) -> None:
        import queue
        import threading

        self.local = local
        self.size = int(size)
        self.send = send
        self.timeout_s = float(timeout_s)
        self._pending: Dict[str, "queue.Queue[dict]"] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------- приём
    def deliver(self, message: dict) -> bool:
        """Ответ стадии пришёл. Ложь — ответ никто не ждёт (опоздал)."""
        call_id = str(message.get("call_id") or "")
        with self._lock:
            waiter = self._pending.get(call_id)
        if waiter is None:
            logger.warning("ответ на %s никто не ждёт", call_id or "?")
            return False
        waiter.put(message)
        return True

    # --------------------------------------------------------- вызовы
    def _call(self, stage: int, message: dict, *, timeout_s: Optional[float] = None) -> dict:
        import queue
        import uuid

        if stage == 0:
            return handle(self.local, message)
        timeout_s = self.timeout_s if timeout_s is None else float(timeout_s)
        call_id = uuid.uuid4().hex[:16]
        waiter: "queue.Queue[dict]" = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[call_id] = waiter
        try:
            self.send({**message, "call_id": call_id, "target_stage": stage})
            try:
                reply = waiter.get(timeout=timeout_s)
            except queue.Empty:
                raise TimeoutError(
                    f"стадия {stage} не ответила на {message.get('kind')} за "
                    f"{timeout_s:g} с") from None
        finally:
            with self._lock:
                self._pending.pop(call_id, None)
        if reply.get("error"):
            raise RuntimeError(f"стадия {stage}: {reply['error']}")
        return reply.get("answer") or {}

    def forward(self, stage: int, message: dict) -> dict:
        return self._call(stage, {**message, "kind": "train_forward"})

    def backward(self, stage: int, message: dict) -> dict:
        return self._call(stage, {**message, "kind": "train_backward"})

    def norms(self) -> List[float]:
        return [float(self._call(i, {"kind": "train_norm"})["norm_squared"])
                for i in range(self.size)]

    def step(self, *, lr: float, grad_scale: float) -> None:
        for i in range(self.size):
            self._call(i, {"kind": "train_step", "lr": lr, "grad_scale": grad_scale})

    def save(self, directory: str) -> None:
        # Каждая стадия пишет в СВОЙ каталог результатов: `dir` — относительный
        # путь внутри него, одинаковый у всех.
        for i in range(self.size):
            self._call(i, {"kind": "train_save", "dir": directory})

    def collect(self) -> List[Dict[str, object]]:
        import torch

        return [unpack(torch, self._call(i, {"kind": "train_collect"})["tensors"])
                for i in range(self.size)]

    def wait_ready(self, *, patience_s: float = 1800.0, every_s: float = 5.0) -> None:
        """Дождаться, пока все стадии поднимутся.

        Стадия грузит веса минутами, а сообщение, пришедшее до готовности,
        она отбрасывает (не отвечает). Первый же микробатч, отправленный
        раньше времени, ждал бы полного таймаута шага и падал. Поэтому
        сначала — короткие пробы с повтором, и только потом работа.
        """
        import time

        deadline = time.monotonic() + patience_s
        for stage in range(1, self.size):
            while True:
                try:
                    self._call(stage, {"kind": "train_ping"}, timeout_s=every_s)
                    break
                except TimeoutError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            f"стадия {stage} не поднялась за {patience_s:g} с") from None
                    logger.info("стадия %d ещё не готова, жду", stage)

    def finish(self) -> None:
        """Сказать стадиям, что обучение окончено: им можно выходить."""
        for i in range(1, self.size):
            try:
                self.send({"kind": "train_finish", "target_stage": i})
            except Exception:
                logger.warning("стадии %d не сказали про конец", i, exc_info=True)


def reply_for(stage, message: dict) -> dict:
    """Что стадия отвечает голове на одно сообщение — с той же меткой
    вызова. Ошибка едет текстом: голова обязана узнать причину, а не ждать
    ответа, которого не будет."""
    call_id = message.get("call_id")
    try:
        answer = handle(stage, message)
    except Exception as exc:
        logger.exception("стадия обучения не справилась с %s", message.get("kind"))
        return {"kind": "train_reply", "call_id": call_id, "target_stage": 0,
                "error": f"{type(exc).__name__}: {exc}"}
    return {"kind": "train_reply", "call_id": call_id, "target_stage": 0,
            "answer": answer}
