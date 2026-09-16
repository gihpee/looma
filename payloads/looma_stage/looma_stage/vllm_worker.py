"""Воркер vLLM, знающий свой срез. По одному на карту.

Стадия vLLM поднимается через штатный исполнитель vLLM (`MultiprocExecutor`):
он заводит по процессу на каждую видимую карту, собирает между ними
NCCL-группу и режет каждый слой поровну на все карты — tensor parallelism.
Наш класс он берёт по имени (`ParallelConfig.worker_cls`) и строит В КАЖДОМ
процессе, поэтому всё, чем стадия отличается от обычного vLLM, живёт здесь и
накладывается в каждом воркере:

    диапазон слоёв            — `layer_range` на время загрузки;
    нет embed/lm_head у средних — `vllm_patch.allow_missing_ends`;
    первая/последняя по слоям — `replace_pipeline_group`;
    приём/отдача активаций     — `stage_step`: входящие тензоры приходят
                                 аргументом, а не через группу конвейера.

Между машинами при этом ничего не меняется: активации по-прежнему ходят
через агента, и другая машина видит стадию как одну, сколько бы карт под ней
ни было. Пример: узел с 2×A40 (TP=2) и два узла по одной 4090 (TP=1) в одном
конвейере из трёх стадий.

Класс собирается по требованию, а не при импорте: его базовый тип живёт в
vLLM, которого на машине без карты нет, а модуль обязан импортироваться и
там. vLLM берёт его как `looma_stage.vllm_worker.StageWorker` — на это
отвечает `__getattr__` модуля.
"""

from __future__ import annotations

import logging
from typing import Optional

from looma_stage.vllm_runner import (RunnerRefused, layer_range, replace_pipeline_group,
                                    stage_role, stage_runner_class)

logger = logging.getLogger("looma_stage.vllm_worker")

#: Полное имя, под которым vLLM найдёт воркер в каждом процессе.
QUALNAME = "looma_stage.vllm_worker.StageWorker"

#: Ключ в `VllmConfig.additional_config`, под которым стадия передаёт воркерам
#: свой срез. Конфиг vLLM уезжает в каждый процесс целиком — другого канала
#: до воркера при его создании нет.
SETTINGS_KEY = "looma_stage"

_CLASS = None


def __getattr__(name: str):
    if name == "StageWorker":
        return worker_class()
    raise AttributeError(name)


def settings_of(vllm_config) -> dict:
    """Срез этой стадии, как его передал драйвер."""
    extra = getattr(vllm_config, "additional_config", None) or {}
    found = extra.get(SETTINGS_KEY) if isinstance(extra, dict) else None
    if not found:
        raise RunnerRefused(
            f"в конфиге vLLM нет {SETTINGS_KEY!r}: воркер не знает, какие слои "
            "собирать. Стадию поднимают через looma_stage.vllm_engine, а не "
            "напрямую")
    return dict(found)


def worker_class():
    """Класс воркера — один на процесс, собирается при первом обращении."""
    global _CLASS
    if _CLASS is not None:
        return _CLASS

    import torch
    from vllm.v1.worker.gpu_worker import Worker

    from looma_stage import vllm_engine, vllm_patch

    class StageWorker(Worker):
        """Штатный воркер vLLM плюс срез слоёв и шаг с тензорами снаружи."""

        def __init__(self, vllm_config, **kwargs) -> None:
            super().__init__(vllm_config=vllm_config, **kwargs)
            stage = settings_of(vllm_config)
            self.start_layer = int(stage["start_layer"])
            self.end_layer = int(stage["end_layer"])
            self.num_model_layers = int(stage["num_model_layers"])
            self.is_first_stage, self.is_last_stage = stage_role(
                self.start_layer, self.end_layer, self.num_model_layers)
            # Конфиг держится открытым всю жизнь процесса — по той же причине,
            # что и в драйвере (см. vllm_engine._hold_config): части движка
            # спрашивают его сами, вне всякого контекста.
            vllm_engine._hold_config(vllm_config)

        # ------------------------------------------------------ подъём
        def init_device(self) -> None:
            """Карта, NCCL-группа, исполнитель — и наши подмены поверх.

            Штатный `init_device` сам строит `GPUModelRunner`. Нам нужен
            наш `StageRunner`, поэтому на время вызова имя класса в модуле
            vLLM указывает на него: штатный код берёт класс по имени в
            момент вызова. Если в этой версии он импортирован иначе и
            построился обычный — заменяем явно, а не молчим: обычный не
            умеет ни принять входящие тензоры, ни отдать свои.
            """
            vllm_patch.allow_missing_ends(is_first=self.is_first_stage,
                                          is_last=self.is_last_stage)
            runner_class = stage_runner_class(self.start_layer, self.end_layer,
                                              self.num_model_layers)
            import vllm.v1.worker.gpu_model_runner as runners

            original = runners.GPUModelRunner
            runners.GPUModelRunner = runner_class
            try:
                super().init_device()
            finally:
                runners.GPUModelRunner = original

            if not isinstance(self.model_runner, runner_class):
                logger.warning("vLLM построил обычный исполнитель; заменяю на "
                               "исполнитель стадии")
                self.model_runner = runner_class(self.vllm_config, self.device)
            replace_pipeline_group(self.start_layer, self.end_layer,
                                   self.num_model_layers)
            logger.info("воркер %d/%d на %s: слои [%d, %d)", self.rank,
                        self.parallel_config.world_size, self.device,
                        self.start_layer, self.end_layer)

        def load_model(self) -> None:
            with layer_range(self.start_layer, self.end_layer):
                super().load_model()
            built = self.stage_layers_built()
            wanted = self.end_layer - self.start_layer
            if built and built != wanted:
                raise RunnerRefused(
                    f"просили {wanted} слоёв, а собралось {built}: приём "
                    "разошёлся с этой версией vLLM, и считать она будет не то")
            logger.info("загружено слоёв: %s", built or "не удалось сосчитать")

        # ---------------------------------------------- вопросы драйвера
        def stage_layers_built(self) -> int:
            return vllm_engine._count_layers(self.model_runner)

        def stage_cache_room(self) -> int:
            """Сколько на ЭТОЙ карте есть под KV-кэш.

            Драйвер возьмёт наименьшее по картам: раскладка кэша у всех
            воркеров обязана быть одной, иначе блоки под одну и ту же
            последовательность лягут на картах по-разному.
            """
            free, _total = torch.cuda.mem_get_info(self.device.index or 0)
            room = int(free * self.cache_config.gpu_memory_utilization)
            logger.info("под KV-кэш: %.1f ГБ из %.1f ГБ свободных на %s",
                        room / 1024 ** 3, free / 1024 ** 3, self.device)
            return room

        # ---------------------------------------------------------- шаг
        def stage_step(self, scheduler_output, incoming: Optional[dict], *,
                       expected: int):
            """Один шаг над батчем, который выбрал драйвер.

            Входящие тензоры приходят аргументом на КАЖДЫЙ воркер целиком:
            при tensor parallelism скрытое состояние на входе слоя одно на
            всех картах. Результат отдаёт только ранг 0 — у остальных он тот
            же самый, и возить его через очередь незачем.

            На процессор, а не картой: тензор в очереди исполнителя едет
            через pickle, а CUDA-тензор через pickle — это IPC-дескриптор,
            который в чужом процессе без той же карты не откроется.
            """
            from vllm.sequence import IntermediateTensors

            tensors = None
            if incoming is not None and not self.is_first_stage:
                items = incoming.items() if hasattr(incoming, "items") else incoming
                tensors = IntermediateTensors({
                    name: value.to(self.device, non_blocking=True)
                    for name, value in items})
            with torch.inference_mode():
                answer = self.model_runner.execute_model(scheduler_output, tensors)
                if self.rank != 0:
                    # Считать обязан каждый — коллективы NCCL ждут всех, — а
                    # закрыть шаг обязан каждый по той же причине, что и
                    # нулевой: иначе у него упадёт следующий.
                    if self.is_last_stage:
                        vllm_engine._finish_step(self.model_runner)
                    return None
                hidden, logits = vllm_engine.collect(self.model_runner, answer,
                                                     is_last=self.is_last_stage,
                                                     expected=expected)
                if logits is not None:
                    return None, logits.cpu()
                return ({name: value.cpu() for name, value in hidden.tensors.items()},
                        None)

    StageWorker.__module__ = __name__
    StageWorker.__qualname__ = "StageWorker"
    _CLASS = StageWorker
    return StageWorker
