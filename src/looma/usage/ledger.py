"""Сколько кто израсходовал.

Журнал, а не счётчик. Счётчик показывает «сейчас», журнал отвечает на вопрос
«за март» — а именно этот вопрос задают, когда приходит время платить. Разница
не в удобстве: снятая задача уносит свои секунды с собой, и сложить их потом не
из чего.

Три решения, каждое из которых легко не заметить и дорого исправлять.

**Ставка записывается в аренду.** Не берётся из справочника при подсчёте.
Иначе поднятая завтра цена перепишет вчерашние счета, и объяснить клиенту
разницу между тем, что он видел, и тем, что пришло, будет нечем.

**Деньги — в копейках, целыми.** Плавающая точка в счетах даёт
0.1 + 0.2 = 0.30000000000000004, и обнаруживается это в момент, когда спорить
уже поздно.

**Незакрытая аренда — норма, а не сбой.** Узел мог отвалиться, оркестратор
мог перезапуститься. Закрываем по наблюдению — и помечаем, что закрыли именно
так: конец времени в этом случае приблизителен, и при разборе это надо знать.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger("looma.usage")

#: Ресурсы, которые платформа продаёт. Обучение — отдельной строкой: у него
#: своя ставка (или ставка кластера, если своя не задана) и свой смысл в
#: отчёте; смешивать его с арендой кластера значило бы показывать клиенту
#: чужое обучение как его «кластер».
INFERENCE = "looma-inference"
COMPUTE = "looma-compute"
TRAINING = "looma-training"
RESOURCES = (INFERENCE, COMPUTE, TRAINING)

#: Почему аренда закрылась.
RELEASED = "released"    # сняли намеренно — время точное
VANISHED = "vanished"    # группы не стало; закрыто по последнему наблюдению

SECONDS_PER_HOUR = 3600


@dataclass(frozen=True)
class Rate:
    resource: str
    per_hour: int          # копеек за GPU-час
    currency: str = "RUB"

    def as_dict(self) -> dict:
        return {"resource": self.resource, "per_hour": self.per_hour,
                "currency": self.currency}


MILLION = 1_000_000


def token_cost(*, prompt_tokens: int, completion_tokens: int,
               price_in: int, price_out: int) -> int:
    """Стоимость ответа в копейках. Цены — за миллион токенов.

    Вверх, как и у часов: доли копейки на коротких ответах складываются в
    сумму, которую иначе платформа систематически раздаёт бесплатно.
    """
    import math
    raw = (max(0, prompt_tokens) * max(0, price_in)
           + max(0, completion_tokens) * max(0, price_out))
    return math.ceil(raw / MILLION) if raw > 0 else 0


def cost(*, per_hour: int, gpus: int, seconds: float) -> int:
    """Во сколько обошлось. Копейки, целыми, с округлением вверх.

    Вверх, а не к ближайшему: доли копейки за секунду складываются в заметную
    сумму на длинной аренде, и терять их систематически в одну сторону —
    значит незаметно раздавать мощность бесплатно.
    """
    if per_hour <= 0 or gpus <= 0 or seconds <= 0:
        return 0
    total = per_hour * gpus * seconds
    return -(-int(total) // SECONDS_PER_HOUR)


class Ledger:
    """Журнал поверх пула соединений."""

    def __init__(self, database) -> None:
        self.db = database

    # -------------------------------------------------------------- ставки
    async def set_rate(self, resource: str, per_hour: int,
                       currency: str = "RUB") -> Rate:
        if resource not in RESOURCES:
            raise ValueError(f"ресурса {resource!r} не бывает: {', '.join(RESOURCES)}")
        if per_hour < 0:
            raise ValueError("ставка не может быть отрицательной")
        async with self.db.acquire() as connection:
            await connection.execute(
                "INSERT INTO rates (resource, per_hour, currency)"
                " VALUES ($1, $2, $3)"
                " ON CONFLICT (resource) DO UPDATE"
                " SET per_hour = $2, currency = $3, updated_at = now()",
                resource, per_hour, currency)
        return Rate(resource, per_hour, currency)

    async def rate(self, resource: str) -> Rate:
        async with self.db.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT resource, per_hour, currency FROM rates WHERE resource = $1",
                resource)
        if row:
            return Rate(**dict(row))
        # Обучение без своей ставки идёт по ставке кластера: это та же карта
        # на те же часы, и второе число оператору заводить необязательно.
        if resource == TRAINING:
            return Rate(TRAINING, (await self.rate(COMPUTE)).per_hour)
        # Ноль, а не отказ: узел должен работать и до того, как оператор назначил
        # цену. Потребление при этом считается, счёт выходит нулевым, и цену
        # можно назначить потом — записи уже собраны.
        return Rate(resource, 0)

    async def rates(self) -> List[Rate]:
        async with self.db.acquire() as connection:
            rows = await connection.fetch(
                "SELECT resource, per_hour, currency FROM rates ORDER BY resource")
        found = {row["resource"]: Rate(**dict(row)) for row in rows}
        return [found.get(name, Rate(name, 0)) for name in RESOURCES]

    # -------------------------------------------------------------- аренда
    async def open_lease(self, *, account_id: int, resource: str, group_id: str,
                         nodes: int = 0, gpus: int = 0, label: str = "") -> int:
        """Начать считать. Ставка берётся сейчас и запоминается в записи."""
        rate = await self.rate(resource)
        async with self.db.acquire() as connection:
            return int(await connection.fetchval(
                "INSERT INTO leases (account_id, resource, group_id, label,"
                " nodes, gpus, per_hour, currency)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
                account_id, resource, group_id, label, nodes, gpus,
                rate.per_hour, rate.currency))

    async def lease_for(self, group_id: str) -> Optional[int]:
        """Номер открытой аренды этой группы. Нужен, чтобы вернуть вытесненное
        ею — по нему снятое и помечено."""
        async with self.db.acquire() as connection:
            found = await connection.fetchval(
                "SELECT id FROM leases WHERE group_id = $1 AND closed_at IS NULL"
                " ORDER BY opened_at DESC LIMIT 1", group_id)
        return int(found) if found else None

    async def close_lease(self, group_id: str, *, why: str = RELEASED) -> int:
        """Закрыть аренду группы. Возвращает, сколько записей закрылось."""
        async with self.db.acquire() as connection:
            done = await connection.execute(
                "UPDATE leases SET closed_at = now(), closed_why = $2"
                " WHERE group_id = $1 AND closed_at IS NULL", group_id, why)
        return int(done.rsplit(" ", 1)[-1] or 0)

    async def reconcile(self, live_group_ids: Iterable[str]) -> int:
        """Закрыть аренды, которых больше нет среди живых групп.

        Нужна потому, что группа умеет исчезнуть сама: узел отвалился, задача
        упала, оркестратор перезапустился. Без сверки такая аренда тикала бы
        вечно, и счёт вырос бы до бесконечности молча.
        """
        live = list(live_group_ids)
        async with self.db.acquire() as connection:
            done = await connection.execute(
                "UPDATE leases SET closed_at = now(), closed_why = $2"
                " WHERE closed_at IS NULL AND NOT (group_id = ANY($1::text[]))",
                live, VANISHED)
        closed = int(done.rsplit(" ", 1)[-1] or 0)
        if closed:
            logger.info("закрыто по сверке: %d аренд, которых больше нет", closed)
        return closed

    async def open_leases(self, *, account_id: Optional[int] = None,
                          resource: Optional[str] = None) -> List[dict]:
        """Идущие аренды. С `account_id` — только свои: клиент не должен видеть
        чужие, даже в списке."""
        where, args = ["closed_at IS NULL"], []
        if account_id is not None:
            args.append(account_id)
            where.append(f"account_id = ${len(args)}")
        if resource is not None:
            args.append(resource)
            where.append(f"resource = ${len(args)}")
        async with self.db.acquire() as connection:
            rows = await connection.fetch(
                "SELECT id, account_id, resource, group_id, label, nodes, gpus,"
                " per_hour, currency, opened_at FROM leases"
                f" WHERE {' AND '.join(where)} ORDER BY opened_at", *args)
        return [dict(row) for row in rows]

    async def lease_belongs_to(self, group_id: str,
                               account_id: Optional[int]) -> bool:
        """Числится ли эта аренда за этим человеком.

        Проверяется по журналу, а не по тому, что прислал клиент: иначе номер
        группы в адресе даёт власть над чужим кластером.
        """
        if account_id is None:
            return False
        async with self.db.acquire() as connection:
            found = await connection.fetchval(
                "SELECT 1 FROM leases WHERE group_id = $1 AND account_id = $2"
                " AND closed_at IS NULL LIMIT 1", group_id, account_id)
        return bool(found)

    # -------------------------------------------------------------- токены
    async def record_tokens(self, *, account_id: int, model: str,
                            prompt_tokens: int, completion_tokens: int,
                            price_in: int = 0, price_out: int = 0) -> int:
        """Токены одного ответа. Другая единица — поэтому другая таблица:
        складывать часы и токены в один столбец означало бы столбец, смысл
        которого зависит от соседнего.

        `price_in`/`price_out` — копейки за миллион токенов по прайсу на
        момент ответа; стоимость считается здесь и запоминается в записи.
        Возвращает, сколько стоил ответ.
        """
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return 0
        price = token_cost(prompt_tokens=prompt_tokens,
                           completion_tokens=completion_tokens,
                           price_in=price_in, price_out=price_out)
        async with self.db.acquire() as connection:
            await connection.execute(
                "INSERT INTO token_usage (account_id, model, prompt_tokens,"
                " completion_tokens, cost) VALUES ($1, $2, $3, $4, $5)",
                account_id, model, max(0, prompt_tokens), max(0, completion_tokens),
                price)
        return price

    # -------------------------------------------------------------- кредиты
    async def grant(self, *, account_id: int, kopecks: int, note: str = "",
                    granted_by: Optional[int] = None) -> dict:
        """Начислить (или, с минусом, скорректировать). Ноль — не запись."""
        if kopecks == 0:
            raise ValueError("начислять ноль бессмысленно")
        async with self.db.acquire() as connection:
            row = await connection.fetchrow(
                "INSERT INTO credits (account_id, kopecks, note, granted_by)"
                " VALUES ($1, $2, $3, $4) RETURNING id, at",
                account_id, kopecks, note or "", granted_by)
        return {"id": int(row["id"]), "account_id": account_id, "kopecks": kopecks,
                "note": note or "", "granted_by": granted_by, "at": row["at"]}

    async def grants(self, account_id: int, limit: int = 100) -> List[dict]:
        """Журнал начислений одного клиента, свежие первыми."""
        async with self.db.acquire() as connection:
            rows = await connection.fetch(
                "SELECT id, account_id, kopecks, note, granted_by, at FROM credits"
                " WHERE account_id = $1 ORDER BY at DESC LIMIT $2", account_id, limit)
        return [dict(row) for row in rows]

    async def balance(self, account_id: int) -> dict:
        """Сколько осталось: начислено минус израсходовано за всё время.

        Расход считается из журнала, а не хранится: идущая аренда тикает, и
        число, которое видит клиент посреди аренды, должно уменьшаться само.
        """
        spent = await self.report(account_id=account_id)
        async with self.db.acquire() as connection:
            credited = await connection.fetchval(
                "SELECT COALESCE(sum(kopecks), 0) FROM credits WHERE account_id = $1",
                account_id)
        credited = int(credited or 0)
        return {"kopecks": credited - spent["total"], "credited": credited,
                "spent": spent["total"], "currency": "RUB"}

    # --------------------------------------------------------------- отчёт
    async def report(self, *, account_id: Optional[int] = None,
                     since: Optional[datetime] = None,
                     until: Optional[datetime] = None,
                     resource: Optional[str] = None) -> dict:
        """Что израсходовано за период. Без account_id — по всем.

        Незакрытые аренды считаются по «сейчас»: клиент, глядя на свой счёт
        посреди аренды, должен видеть то, что уже натикало, а не ноль.

        `resource` сужает отчёт до одного продукта; `looma-inference` при
        этом означает и аренды инференса, и токены.
        """
        where, args = ["TRUE"], []
        if account_id is not None:
            args.append(account_id)
            where.append(f"account_id = ${len(args)}")
        if since is not None:
            args.append(since)
            where.append(f"opened_at >= ${len(args)}")
        if until is not None:
            args.append(until)
            where.append(f"opened_at < ${len(args)}")
        condition = " AND ".join(where)
        lease_condition = condition
        if resource is not None:
            args.append(resource)
            lease_condition = f"{condition} AND resource = ${len(args)}"
        lease_args = list(args)
        token_args = lease_args[:-1] if resource is not None else lease_args
        want_tokens = resource in (None, INFERENCE)

        async with self.db.acquire() as connection:
            leases = await connection.fetch(
                "SELECT resource, gpus, per_hour, currency, opened_at,"
                " COALESCE(closed_at, now()) AS ended_at, closed_at IS NULL AS running"
                f" FROM leases WHERE {lease_condition}", *lease_args)
            tokens = await connection.fetch(
                "SELECT model, sum(prompt_tokens) AS prompt,"
                " sum(completion_tokens) AS completion, sum(cost) AS cost"
                f" FROM token_usage WHERE {condition.replace('opened_at', 'at')}"
                " GROUP BY model ORDER BY model", *token_args) if want_tokens else []

        by_resource: Dict[str, dict] = {}
        for row in leases:
            seconds = (row["ended_at"] - row["opened_at"]).total_seconds()
            piece = by_resource.setdefault(row["resource"], {
                "resource": row["resource"], "seconds": 0.0, "gpu_seconds": 0.0,
                "cost": 0, "currency": row["currency"], "running": 0, "leases": 0,
            })
            piece["leases"] += 1
            piece["running"] += 1 if row["running"] else 0
            piece["seconds"] += seconds
            piece["gpu_seconds"] += seconds * max(0, row["gpus"])
            piece["cost"] += cost(per_hour=row["per_hour"], gpus=row["gpus"],
                                  seconds=seconds)

        token_lines = [
            {"model": row["model"], "prompt": int(row["prompt"] or 0),
             "completion": int(row["completion"] or 0), "cost": int(row["cost"] or 0)}
            for row in tokens
        ]
        lease_lines = [
            {**piece, "gpu_hours": round(piece["gpu_seconds"] / SECONDS_PER_HOUR, 3)}
            for piece in sorted(by_resource.values(), key=lambda p: p["resource"])
        ]
        return {
            "leases": lease_lines,
            "tokens": token_lines,
            "total": (sum(p["cost"] for p in lease_lines)
                      + sum(t["cost"] for t in token_lines)),
            "currency": "RUB",
        }


def report_csv(report: dict) -> str:
    """Отчёт строками для таблицы: по одной на ресурс и на модель.

    Суммы — в рублях с копейками, а не в копейках: файл открывают в таблице
    люди, а не программы, и «120.50» им понятнее, чем «12050».
    """
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\n")
    writer.writerow(["вид", "ресурс/модель", "аренд", "GPU-часов",
                     "токенов на вход", "токенов на выход", "стоимость, ₽"])
    for line in report.get("leases", []):
        writer.writerow(["аренда", line["resource"], line["leases"],
                         f"{line['gpu_hours']:.3f}", "", "",
                         f"{line['cost'] / 100:.2f}"])
    for line in report.get("tokens", []):
        writer.writerow(["токены", line["model"], "", "", line["prompt"],
                         line["completion"], f"{line.get('cost', 0) / 100:.2f}"])
    writer.writerow(["итого", "", "", "", "", "",
                     f"{report.get('total', 0) / 100:.2f}"])
    return out.getvalue()
