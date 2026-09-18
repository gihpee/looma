"""Задания на дообучение: чем подняли, чем кончилось.

Само обучение — группа задач у агентов (`AgentHub.groups`); здесь то, чего у
группы нет: кто заказал, что заказал, чем кончилось и где адаптер. Без этой
записи перезапуск оркестратора превращал бы идущее обучение в безымянную
группу, а законченное — в ничто.

Есть реализация на Postgres и в памяти (для тестов и оркестратора без базы):
интерфейс один.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("looma.usage.training")

RUNNING, DONE, FAILED, STOPPED = "running", "done", "failed", "stopped"


@dataclass
class TrainingJob:
    group_id: str
    label: str
    request: dict
    account_id: Optional[int]
    state: str = RUNNING
    result: Optional[dict] = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def as_dict(self) -> dict:
        return {"group_id": self.group_id, "label": self.label, "state": self.state,
                "request": self.request, "result": self.result, "error": self.error,
                "account_id": self.account_id, "created_at": self.created_at,
                "finished_at": self.finished_at}


class MemoryTrainingJobs:
    """В памяти: тесты и оркестратор без базы."""

    def __init__(self) -> None:
        self._jobs: Dict[str, TrainingJob] = {}

    async def remember(self, *, group_id: str, label: str, request: dict,
                       account_id: Optional[int]) -> TrainingJob:
        job = TrainingJob(group_id=group_id, label=label, request=request,
                          account_id=account_id)
        self._jobs[group_id] = job
        return job

    async def get(self, group_id: str) -> Optional[TrainingJob]:
        return self._jobs.get(group_id)

    async def list(self, *, account_id: Optional[int] = None) -> List[TrainingJob]:
        jobs = [j for j in self._jobs.values()
                if account_id is None or j.account_id == account_id]
        return sorted(jobs, key=lambda j: -j.created_at)

    async def finish(self, group_id: str, *, state: str, result: Optional[dict] = None,
                     error: str = "") -> None:
        job = self._jobs.get(group_id)
        if job is None or job.state != RUNNING:
            return
        job.state, job.result, job.error = state, result, error
        job.finished_at = time.time()


class TrainingJobs:
    """То же на Postgres."""

    def __init__(self, database) -> None:
        self.db = database

    async def remember(self, *, group_id: str, label: str, request: dict,
                       account_id: Optional[int]) -> TrainingJob:
        async with self.db.acquire() as connection:
            await connection.execute(
                "INSERT INTO training_jobs (group_id, label, request, account_id)"
                " VALUES ($1, $2, $3::jsonb, $4)"
                " ON CONFLICT (group_id) DO UPDATE SET label = $2, request = $3::jsonb",
                group_id, label, json.dumps(request), account_id)
        return TrainingJob(group_id=group_id, label=label, request=request,
                           account_id=account_id)

    async def get(self, group_id: str) -> Optional[TrainingJob]:
        async with self.db.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM training_jobs WHERE group_id = $1", group_id)
        return _made(row) if row else None

    async def list(self, *, account_id: Optional[int] = None) -> List[TrainingJob]:
        async with self.db.acquire() as connection:
            if account_id is None:
                rows = await connection.fetch(
                    "SELECT * FROM training_jobs ORDER BY created_at DESC LIMIT 500")
            else:
                rows = await connection.fetch(
                    "SELECT * FROM training_jobs WHERE account_id = $1"
                    " ORDER BY created_at DESC LIMIT 500", account_id)
        return [_made(row) for row in rows]

    async def finish(self, group_id: str, *, state: str, result: Optional[dict] = None,
                     error: str = "") -> None:
        async with self.db.acquire() as connection:
            await connection.execute(
                "UPDATE training_jobs SET state = $2, result = $3::jsonb, error = $4,"
                " finished_at = now() WHERE group_id = $1 AND state = 'running'",
                group_id, state, json.dumps(result) if result is not None else None, error)


def _made(row) -> TrainingJob:
    request = row["request"]
    result = row["result"]
    return TrainingJob(
        group_id=row["group_id"], label=row["label"],
        request=json.loads(request) if isinstance(request, str) else dict(request or {}),
        account_id=row["account_id"], state=row["state"],
        result=(json.loads(result) if isinstance(result, str) else result) if result else None,
        error=row["error"] or "",
        created_at=row["created_at"].timestamp() if row["created_at"] else 0.0,
        finished_at=row["finished_at"].timestamp() if row["finished_at"] else None)
