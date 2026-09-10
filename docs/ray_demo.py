"""Looma Float. Что умеет кластер, собранный из чужих домашних машин.

Клиент уже подключён (looma-connect), поэтому запуск такой:

    python demo.py

Каждое число здесь измерено на живом кластере. Ничего не берётся из
конфигурации и ничего не подставляется «для красоты»: если torch на узлах
не стоит, тест честно пропускается и говорит, что вписать в поле
«библиотеки» при следующем запуске.
"""

import hashlib
import json
import os
import socket
import time
from pathlib import Path

import ray
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy

ADDRESS = "ray://127.0.0.1:10001"
LINE = "-" * 74

# Библиотеки на узлах: два способа, и они про разное.
#
#   1. Поле «библиотеки» в консоли при создании кластера. Ставится один раз
#      на узел, до старта Ray, и лежит в кэше узла. Так надо ставить тяжёлое
#      и постоянное: torch, cuda-колёса.
#
#   2. RUNTIME_ENV ниже. Ray ставит это сам, в момент запуска задания, на
#      каждом узле, где окажется работа. Так надо ставить лёгкое и меняющееся
#      от запуска к запуску — кластер при этом трогать не нужно.
#
# Пустой словарь означает «ничего не ставить, брать что есть на узле».
RUNTIME_ENV = {}

# Хотите проверить второй способ, не пересобирая кластер — раскомментируйте.
# Первый запуск на каждом узле будет заметно дольше: Ray заводит отдельное
# окружение и качает пакет.
#
# Узлам для этого нужен пакет virtualenv: плагин pip у Ray заводит окружение
# задания через него. Кластеры, поднятые Looma начиная с версии, где virtualenv
# попал в требования, получают его сами. На кластере постарше ray.init просто
# откажет — демо это увидит, скажет и продолжит без runtime_env.
# RUNTIME_ENV = {"pip": ["cowsay==6.1"]}
#
# Через runtime_env можно и torch, но осознанно: это ~2.5 ГБ на узел на
# каждый новый набор пакетов, без кэша между разными наборами.
# RUNTIME_ENV = {"pip": ["torch", "numpy"]}


# --------------------------------------------------------------- служебное
def title(text):
    print(f"\n{LINE}\n  {text}\n{LINE}")


def on(node_id):
    """Положить задачу именно на этот узел, а не куда решит планировщик.

    soft=False означает «или сюда, или никуда»: без этого Ray имеет право
    увести задачу на свободный узел, и весь замер потеряет смысл.
    """
    return NodeAffinitySchedulingStrategy(node_id=node_id, soft=False)


def nodes_by_rank():
    """Узлы в порядке рангов.

    Порядок ray.nodes() ничем не задан, поэтому опираемся на адреса: узел
    ранга N всегда поднимается на 127.0.0.<N+2>, и этот адрес означает одно
    и то же на каждой машине группы.
    """
    alive = {n["NodeManagerAddress"]: n for n in ray.nodes() if n["Alive"]}
    ordered = [alive.get(f"127.0.0.{2 + rank}") for rank in range(len(alive))]
    if None in ordered:
        raise SystemExit(f"узлы записаны не своими адресами: {sorted(alive)}")
    return ordered


def short(node_id):
    return node_id[:8]


# ------------------------------------------------------ удалённые функции
# ВАЖНО: имена удалённых функций и классов — только латиница. Ray передаёт
# имя jobs как ASCII, и кириллическое имя роняет воркер на UnicodeEncodeError.

@ray.remote(num_cpus=1)
def whoami():
    """Кто выполняет эту задачу."""
    return socket.gethostname(), ray.get_runtime_context().get_node_id()


@ray.remote(num_cpus=1)
def burn(rounds):
    """Занять ядро настоящей работой и сказать, где это было.

    Работа именно счётная, а не sleep: спящая задача держит слот Ray, но не
    показывает, насколько машина быстрая, — а без этого нечем делить работу.
    """
    started = time.perf_counter()
    data = b"looma"
    for _ in range(rounds):
        data = hashlib.sha256(data).digest()
    return {"seconds": time.perf_counter() - started,
            "host": socket.gethostname(),
            "node": ray.get_runtime_context().get_node_id()}


@ray.remote(num_cpus=1)
def produce(megabytes):
    """Создать блок данных прямо на этом узле и оставить его тут."""
    return os.urandom(megabytes * 1024 * 1024)


@ray.remote(num_cpus=1)
def consume(chunk):
    """Принять блок и подтвердить размер. Приём и есть измеряемая передача."""
    return len(chunk)


@ray.remote(num_cpus=1)
def has_modules(names):
    """Что из заказанного реально доехало до этого узла и какой версии."""
    import importlib
    found = {}
    for name in names:
        try:
            module = importlib.import_module(name)
        except ImportError as error:
            found[name] = f"НЕТ ({error.__class__.__name__})"
        else:
            found[name] = getattr(module, "__version__", "есть, версия не заявлена")
    return found


@ray.remote(num_gpus=1)
def gpu_probe():
    """Какая карта на этом узле и жива ли она."""
    host = socket.gethostname()
    try:
        import torch
    except ImportError:
        return {"host": host, "torch": False}
    if not torch.cuda.is_available():
        return {"host": host, "torch": True, "cuda": False}
    props = torch.cuda.get_device_properties(0)
    return {
        "host": host,
        "torch": True,
        "cuda": True,
        "name": props.name,
        "memory_gb": round(props.total_memory / 1024 ** 3, 1),
    }


@ray.remote(num_gpus=1)
def gpu_matmul(size, repeats):
    """Перемножить матрицы и вернуть достигнутую скорость в терафлопсах."""
    import torch
    host = socket.gethostname()
    a = torch.randn(size, size, device="cuda", dtype=torch.float16)
    b = torch.randn(size, size, device="cuda", dtype=torch.float16)
    torch.matmul(a, b)                 # прогрев: первый вызов поднимает ядра
    torch.cuda.synchronize()
    started = time.perf_counter()
    for _ in range(repeats):
        torch.matmul(a, b)
    torch.cuda.synchronize()
    spent = time.perf_counter() - started
    flops = 2 * (size ** 3) * repeats  # умножение N x N стоит 2N^3 операций
    return {"host": host, "tflops": flops / spent / 1e12, "seconds": spent}


@ray.remote(num_gpus=1)
def stage(payload, layers):
    """Кусок конвейера: прогнать вектор через несколько слоёв на своей карте."""
    import torch
    x = torch.as_tensor(payload, device="cuda", dtype=torch.float16)
    width = x.shape[-1]
    weight = torch.randn(width, width, device="cuda", dtype=torch.float16) / width
    for _ in range(layers):
        x = torch.tanh(x @ weight)
    return x.cpu().numpy()


# ------------------------------------------------------------- сценарии
def inventory(report):
    title("1. ИЗ ЧЕГО СОБРАН КЛАСТЕР")
    total = ray.cluster_resources()
    nodes = nodes_by_rank()

    print(f"  узлов: {len(nodes)}\n")
    print(f"    {'ранг':<6}{'адрес':<14}{'узел':<12}{'CPU':>5}{'GPU':>6}{'RAM, ГБ':>12}")
    for rank, node in enumerate(nodes):
        res = node["Resources"]
        print(f"    {rank:<6}{node['NodeManagerAddress']:<14}"
              f"{short(node['NodeID']):<12}"
              f"{res.get('CPU', 0):>5.0f}{res.get('GPU', 0):>6.0f}"
              f"{res.get('memory', 0) / 1024 ** 3:>12.1f}")

    print(f"\n    {'ИТОГО':<32}{total.get('CPU', 0):>5.0f}"
          f"{total.get('GPU', 0):>6.0f}{total.get('memory', 0) / 1024 ** 3:>12.1f}")

    cards = sorted(k.split(":", 1)[1] for k in total if k.startswith("accelerator_type:"))
    if cards:
        print(f"\n  карты: {', '.join(cards)}")
    print("\n  Ни у одной из этих машин нет публичного адреса и проброшенных портов.")
    print("  Друг для друга они выглядят как соседние узлы в одной подсети.")

    report["total"] = dict(total)
    report["nodes"] = [
        {"rank": rank, "address": n["NodeManagerAddress"], "id": n["NodeID"],
         "cpu": n["Resources"].get("CPU", 0), "gpu": n["Resources"].get("GPU", 0),
         "ram_gb": n["Resources"].get("memory", 0) / 1024 ** 3}
        for rank, n in enumerate(nodes)
    ]
    return nodes, total


def calibrate(nodes, report):
    """Померить каждую машину по отдельности. Без этого делить работу нечем."""
    title("2. МАШИНЫ РАЗНЫЕ ПО СИЛЕ")
    print("  гоним одинаковый кусок работы на каждой машине и смотрим,")
    print("  за сколько она справится\n")

    probe_rounds = 200_000
    results = ray.get([burn.options(scheduling_strategy=on(n["NodeID"])).remote(probe_rounds)
                       for n in nodes])

    speeds = []
    for rank, result in enumerate(results):
        rate = probe_rounds / result["seconds"]
        speeds.append({"rank": rank, "host": result["host"], "node": result["node"],
                       "seconds": result["seconds"], "rate": rate})
        print(f"    ранг {rank}  {result['host']:<22}{result['seconds']:>6.2f} с"
              f"   {rate / 1000:>7.0f} тыс. хешей/с")

    fastest = max(speeds, key=lambda s: s["rate"])
    slowest = min(speeds, key=lambda s: s["rate"])
    gap = fastest["rate"] / slowest["rate"]
    print(f"\n  Самая быстрая машина ({fastest['host']}) сильнее самой медленной")
    print(f"  ({slowest['host']}) в {gap:.1f} раза. Это не мелочь: если делить")
    print("  работу поровну, весь кластер будет ждать медленную. Раздел 4 про это.")

    report["speed"] = speeds
    return speeds


def placement(nodes, speeds, report):
    """Куда Ray кладёт задачи сам и как это изменить."""
    title("3. КУДА RAY КЛАДЁТ ЗАДАЧИ")
    fastest = max(s["rate"] for s in speeds)
    rounds = max(1000, int(0.30 * fastest))     # ~0.3 с на самой быстрой машине
    tasks = 12
    hosts = {s["node"]: s["host"] for s in speeds}
    print(f"  {tasks} задач по ~0.3 с каждая (работа настоящая, не sleep)\n")

    def run(strategy, label):
        options = {"scheduling_strategy": strategy} if strategy else {}
        results = ray.get([burn.options(**options).remote(rounds) for _ in range(tasks)])
        counted = {}
        for result in results:
            counted[result["node"]] = counted.get(result["node"], 0) + 1
        rows = [{"rank": rank, "host": hosts.get(node["NodeID"], node["NodeManagerAddress"]),
                 "count": counted.get(node["NodeID"], 0)}
                for rank, node in enumerate(nodes)]
        print(f"  {label}")
        for row in rows:
            print(f"    ранг {row['rank']}  {row['host']:<22}{row['count']:>4}"
                  f"  {'#' * row['count']}")
        print()
        return rows

    default_rows = run(None, "как решает Ray сам:")
    spread_rows = run("SPREAD", "с явной просьбой разложить (SPREAD):")

    busy = sum(1 for row in default_rows if row["count"])
    if busy < 2:
        print("  ! сам Ray всё сложил на одну машину. Так бывает, когда задачи")
        print("    короче, чем время на их раздачу: узел успевает освободиться")
        print("    раньше, чем планировщик решит отдать работу соседу.")
    else:
        print("  Планировщик сам занял обе машины: как только на первой кончилось")
        print("  свободное ядро, следующая задача уехала на вторую.")
    print("  SPREAD — это когда раскладку надо гарантировать, а не надеяться:")
    print("  Ray раздаёт по кругу, не дожидаясь, пока узел упрётся в потолок.")

    report["placement"] = {"tasks": tasks, "rounds": rounds,
                           "default": default_rows, "spread": spread_rows}


def splitting(nodes, speeds, report):
    """Главный раздел: как делить работу между неравными машинами."""
    title("4. КАК ДЕЛИТЬ РАБОТУ МЕЖДУ НЕРАВНЫМИ МАШИНАМИ")
    fastest = max(speeds, key=lambda s: s["rate"])
    rates = [s["rate"] for s in speeds]
    unit = max(1000, int(0.25 * fastest["rate"]))   # ~0.25 с на быстрой машине
    units = 12
    print(f"  {units} одинаковых кусков работы, по ~0.25 с на самой быстрой машине.")
    print("  Один и тот же объём, три разных способа разложить.\n")

    def run(shares, label):
        jobs = []
        for rank, share in enumerate(shares):
            jobs += [burn.options(scheduling_strategy=on(nodes[rank]["NodeID"])).remote(unit)
                       for _ in range(share)]
        started = time.perf_counter()
        ray.get(jobs)
        spent = time.perf_counter() - started
        layout = "/".join(str(s) for s in shares)
        print(f"    {label:<34}{layout:>8}{spent:>9.2f} с")
        return {"label": label, "shares": shares, "seconds": spent}

    # 1. Только быстрая машина: столько дал бы один узел, без кластера вообще.
    only_fastest = [0] * len(nodes)
    only_fastest[fastest["rank"]] = units
    alone = run(only_fastest, "только самая быстрая")

    # 2. Поровну — то, что кажется очевидным и что почти всегда неверно.
    evenly = [units // len(nodes)] * len(nodes)
    evenly[0] += units - sum(evenly)
    equal = run(evenly, "поровну между машинами")

    # 3. По силе: доля куска пропорциональна измеренной скорости узла.
    total_rate = sum(rates)
    by_strength = [max(1, round(units * rate / total_rate)) for rate in rates]
    by_strength[rates.index(max(rates))] += units - sum(by_strength)
    weighted = run(by_strength, "по силе машин (пропорционально)")

    gain = alone["seconds"] / weighted["seconds"] if weighted["seconds"] else 0
    ceiling = sum(rates) / max(rates)
    print(f"\n  Против одной только быстрой машины:")
    print(f"    поровну          {alone['seconds'] / equal['seconds']:>6.2f}x")
    print(f"    по силе          {gain:>6.2f}x     потолок {ceiling:.2f}x")
    print("\n  Делить поровну между неравными машинами — верный способ сделать")
    print("  кластер МЕДЛЕННЕЕ одной машины: все ждут самую слабую. Потолок")
    print("  считается как сумма скоростей, делённая на скорость лучшей: вторая")
    print("  машина добавляет ровно столько, сколько она сама умеет.")

    report["splitting"] = {"unit_rounds": unit, "units": units,
                           "runs": [alone, equal, weighted],
                           "speedup": gain, "ceiling": ceiling,
                           "equal_speedup": alone["seconds"] / equal["seconds"]}
    return gain


def bandwidth(nodes, report):
    title("5. С КАКОЙ СКОРОСТЬЮ УЗЛЫ ОБМЕНИВАЮТСЯ ДАННЫМИ")
    if len(nodes) < 2:
        return None
    a, b = nodes[0]["NodeID"], nodes[1]["NodeID"]
    print("  данные рождаются на ранге 0 и забираются рангом 1;")
    print("  клиент в передаче не участвует — меряем именно канал между машинами\n")

    points, best = [], 0.0
    for mb in (1, 8, 32):
        ref = produce.options(scheduling_strategy=on(a)).remote(mb)
        ray.wait([ref], fetch_local=False)     # дождаться, но не тянуть к себе
        started = time.perf_counter()
        ray.get(consume.options(scheduling_strategy=on(b)).remote(ref))
        spent = max(time.perf_counter() - started, 1e-6)
        mbits = mb * 8 / spent
        best = max(best, mbits)
        points.append({"mb": mb, "seconds": spent, "mbits": mbits})
        print(f"    {mb:>3} МБ  за {spent:>7.3f} с   {mbits:>9.1f} Мбит/с")
        del ref

    if points[0]["mbits"] * 2 < best:
        print("\n  Мелкие блоки идут медленнее крупных: на них уходит фиксированная")
        print("  плата за раскачку соединения. Полосой считаем лучшее число.")

    # Сколько весит одна пересылка для каждой стратегии — считаем, а не помним.
    HIDDEN, DTYPE = 4096, 2                    # ширина скрытого состояния, fp16
    token_bytes = HIDDEN * DTYPE               # 8 КБ на токен на стык
    print(f"\n  Рабочая полоса: {best:.0f} Мбит/с. Это число решает всё:\n")
    verdicts = []
    for what, label, need_bytes, budget, unit in (
        ("обучение 7B, DDP", "обучение\n7B, DDP", 14 * 1024 ** 3, 1.0, "на шаг"),
        ("тензорный параллелизм", "тензорный\nпараллелизм", 200 * 1024 ** 2, 0.05, "на слой"),
        ("конвейер по слоям", "конвейер\nпо слоям", token_bytes, 0.1, "на токен"),
    ):
        spent = need_bytes * 8 / (best * 1e6)
        ok = spent <= budget
        verdicts.append({"what": what, "label": label, "bytes": need_bytes,
                         "seconds": spent, "budget": budget, "unit": unit, "ok": ok})
        print(f"    {what:<24}{_span(spent):>10} {unit:<9} -> "
              f"{'работает' if ok else 'не поедет'}")

    tokens = best * 1e6 / (token_bytes * 8)
    print(f"\n  Потолок конвейера только из-за сети: {tokens:.0f} токенов/с")
    print(f"  (скрытое состояние {HIDDEN} в fp16 — это {token_bytes / 1024:.0f} КБ на стык).")
    print("  Обучение и тензорный параллелизм на таком канале не живут: им нужно")
    print("  двигать мегабайты и гигабайты там, где конвейеру хватает килобайт.")

    report["bandwidth"] = {"points": points, "best_mbits": best, "verdicts": verdicts,
                           "token_bytes": token_bytes, "tokens_per_second": tokens}
    return best


def gpus(nodes, report):
    title("6. ОБЕ КАРТЫ СЧИТАЮТ ОДНОВРЕМЕННО")
    with_gpu = [n for n in nodes if n["Resources"].get("GPU", 0) >= 1]
    if not with_gpu:
        print("  в кластере нет узлов с GPU — тест пропущен")
        return None, with_gpu

    probes = ray.get([gpu_probe.options(scheduling_strategy=on(n["NodeID"])).remote()
                      for n in with_gpu])
    mismatch = False
    for probe in probes:
        if not probe["torch"]:
            print(f"    {probe['host']:<26} torch не установлен")
        elif not probe["cuda"]:
            mismatch = True
            print(f"    {probe['host']:<26} torch есть, cuda НЕ ВИДИТ карту")
        else:
            print(f"    {probe['host']:<26} {probe['name']}  {probe['memory_gb']} ГБ")

    if mismatch:
        print("\n  ! Ray на этом узле уверен, что карта есть — она записана в его")
        print("    ресурсы и по ней узел брал задания. torch её при этом не видит.")
        print("    Так выглядит машина, у которой драйвер отвалился или")
        print("    переустанавливается уже ПОСЛЕ того, как узел вошёл в кластер:")
        print("    ресурсы Ray переписываются только при старте узла.")
        print("    Лечится перезапуском агента на той машине.")

    ready = [n for n, p in zip(with_gpu, probes) if p["torch"] and p.get("cuda")]
    report["gpu_probes"] = probes
    if not ready:
        print("\n  ! рабочих карт не осталось — тест пропущен")
        return None, ready
    if len(ready) < len(with_gpu):
        print(f"\n  Считать будем на том, что живо: карт {len(ready)} из {len(with_gpu)}.")

    print("\n  умножаем матрицы 8192x8192 в fp16 сразу на всех живых картах\n")
    started = time.perf_counter()
    results = ray.get([gpu_matmul.options(scheduling_strategy=on(n["NodeID"])).remote(8192, 30)
                       for n in ready])
    wall = time.perf_counter() - started

    total_tflops = sum(r["tflops"] for r in results)
    for result in results:
        print(f"    {result['host']:<26}{result['tflops']:>7.1f} TFLOPS")
    print(f"    {'СУММА':<26}{total_tflops:>7.1f} TFLOPS   (стена {wall:.1f} с)")

    report["gpu"] = {"cards": results, "total": total_tflops, "wall": wall,
                     "cards_seen": len(with_gpu), "cards_alive": len(ready)}
    return total_tflops, ready


def pipeline(ready, report):
    title("7. КОНВЕЙЕР: МОДЕЛЬ, РАЗРЕЗАННАЯ ПО МАШИНАМ")
    if len(ready) < 2:
        print("  нужны две машины с рабочей картой — тест пропущен")
        return
    print("  так Looma и гоняет большие модели: первая половина слоёв на одной")
    print("  карте, вторая на другой, между ними по сети идёт только вектор\n")

    import numpy as np

    batch, width, layers = 8, 4096, 24
    payload = np.random.randn(batch, width).astype("float16")
    hop_kb = payload.nbytes / 1024

    times = []
    for _ in range(3):
        started = time.perf_counter()
        mid = stage.options(scheduling_strategy=on(ready[0]["NodeID"])).remote(payload, layers)
        out = stage.options(scheduling_strategy=on(ready[1]["NodeID"])).remote(mid, layers)
        result = ray.get(out)
        times.append(time.perf_counter() - started)
        del mid, out

    print(f"    {2 * layers} слоёв на двух картах, {batch} строк по {width}")
    print(f"    через сеть за проход: {hop_kb:.0f} КБ")
    print(f"    проходы: {', '.join(f'{t:.2f} с' for t in times)}   лучший {min(times):.2f} с")
    print(f"    форма результата: {result.shape}, конечных значений "
          f"{int(np.isfinite(result).all())}")
    print("\n  Вектор между слоями — килобайты. Именно поэтому такая нарезка")
    print("  живёт на домашних каналах, а тензорный параллелизм — нет.")

    report["pipeline"] = {"times": times, "hop_kb": hop_kb, "layers": 2 * layers}


def resilience(nodes, report):
    title("8. ОШИБКА НА ЧУЖОЙ МАШИНЕ НЕ ВАЛИТ ЗАДАЧУ")

    @ray.remote(num_cpus=1, max_retries=0)
    def explode():
        raise RuntimeError("узел не справился")

    ok = ray.get(whoami.options(scheduling_strategy=on(nodes[0]["NodeID"])).remote())
    print(f"    обычная задача на ранге 0: {ok[0]}")
    caught = ""
    try:
        ray.get(explode.options(scheduling_strategy=on(nodes[-1]["NodeID"])).remote())
    except ray.exceptions.RayTaskError as error:
        caught = type(error).__name__
        first = str(error).strip().splitlines()[-1]
        print(f"    падение на ранге {len(nodes) - 1}: поймано как {caught}")
        print(f"      {first}")
    ok = ray.get(whoami.options(scheduling_strategy=on(nodes[-1]["NodeID"])).remote())
    print(f"    тот же узел сразу после падения: {ok[0]} — жив")
    report["resilience"] = {"caught": caught, "node_alive_after": ok[0]}


def libraries(nodes, fallback=""):
    """Проверить, что заказанное через RUNTIME_ENV доехало до каждого узла."""
    title("9. БИБЛИОТЕКИ ЧЕРЕЗ RUNTIME_ENV")
    if fallback:
        print(f"  не сработало: {fallback}.")
        print("  Задание пошло на том, что уже стоит на узлах, — поэтому")
        print("  заказанного там нет и проверять нечего.")
        print("  Лечится пересозданием кластера на свежей версии.")
        return
    if not RUNTIME_ENV:
        print("  RUNTIME_ENV пуст — задание идёт на том, что уже стоит на узлах.")
        print("  Чтобы попробовать второй способ, раскомментируйте строку в шапке.")
        return

    wanted = RUNTIME_ENV.get("pip", [])
    print(f"  заказано на задание: {', '.join(wanted) or '—'}")
    print("  проверяем на каждом узле отдельно\n")
    modules = [name.split("==")[0].split("[")[0].replace("-", "_") for name in wanted]
    for rank, node in enumerate(nodes):
        found = ray.get(
            has_modules.options(scheduling_strategy=on(node["NodeID"])).remote(modules))
        for module, version in found.items():
            print(f"    ранг {rank}  {module:<20}{version}")
    print("\n  Кластер при этом не пересоздавался: набор пакетов меняется")
    print("  от запуска к запуску, машины провайдеров об этом не знают.")


# --------------------------------------------------------------- графики
PALETTE = {"ink": "#22303a", "muted": "#8e9aa3", "grid": "#dfe4e8",
           "accent": "#1f6f8b", "warm": "#c26a3c", "good": "#3f7d5c"}


def _span(seconds):
    """Секунды человеку: миллисекунды там, где секунды выглядят как ноль."""
    if seconds < 1:
        return f"{seconds * 1000:.0f} мс"
    if seconds < 600:
        return f"{seconds:.1f} с"
    return f"{seconds / 60:.0f} мин"


def _style(ax, title_text, subtitle=""):
    ax.set_title(title_text, fontsize=12, fontweight="bold",
                 color=PALETTE["ink"], loc="left", pad=16 if subtitle else 8)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=8.5,
                color=PALETTE["muted"], va="bottom")
    ax.set_facecolor("white")
    ax.grid(axis="y", color=PALETTE["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(PALETTE["grid"])
    ax.tick_params(colors=PALETTE["muted"], labelsize=9)


def plot_speed(ax, report):
    rows = report["speed"]
    labels = [f"ранг {r['rank']}\n{r['host'][:16]}" for r in rows]
    values = [r["rate"] / 1000 for r in rows]
    fastest = max(values)
    colors = [PALETTE["accent"] if v == fastest else PALETTE["muted"] for v in values]
    bars = ax.bar(labels, values, color=colors, width=0.5)
    ax.bar_label(bars, fmt="%.0f", padding=3, fontsize=9, color=PALETTE["ink"])
    ax.set_ylabel("тыс. хешей/с")
    ax.set_ylim(0, fastest * 1.25 or 1)
    _style(ax, "Машины разные по силе",
           f"разрыв {fastest / min(values):.1f}x — работу нельзя делить поровну")


def plot_placement(ax, report):
    data = report["placement"]
    labels = [f"ранг {r['rank']}\n{r['host'][:16]}" for r in data["default"]]
    spots = range(len(labels))
    width = 0.36
    left = ax.bar([i - width / 2 for i in spots], [r["count"] for r in data["default"]],
                  width, label="как решает Ray", color=PALETTE["muted"])
    right = ax.bar([i + width / 2 for i in spots], [r["count"] for r in data["spread"]],
                   width, label="SPREAD", color=PALETTE["accent"])
    for bars in (left, right):
        ax.bar_label(bars, padding=3, fontsize=8.5, color=PALETTE["ink"])
    ax.set_xticks(list(spots), labels)
    ax.set_ylabel("задач взято")
    ax.set_ylim(0, data["tasks"] * 1.2)
    ax.legend(frameon=False, fontsize=8.5, labelcolor=PALETTE["muted"])
    _style(ax, "Куда попали задачи", f"{data['tasks']} задач по ~0.3 с")


def plot_splitting(ax, report):
    data = report["splitting"]
    labels = [r["label"].replace(" между машинами", "").replace(" машин (пропорционально)", "")
              for r in data["runs"]]
    values = [r["seconds"] for r in data["runs"]]
    best = min(values)
    colors = [PALETTE["good"] if v == best else PALETTE["warm"] for v in values]
    bars = ax.bar(labels, values, color=colors, width=0.5)
    ax.bar_label(bars, fmt="%.1f с", padding=3, fontsize=9, color=PALETTE["ink"])
    ax.set_ylabel("секунд на весь объём")
    ax.set_ylim(0, max(values) * 1.3 or 1)
    _style(ax, f"Как делить работу: {data['speedup']:.2f}x против {data['ceiling']:.2f}x",
           "зелёное — быстрее всех; делить поровну хуже, чем не делить вовсе")


def plot_bandwidth(ax, report):
    data = report["bandwidth"]
    labels = [f"{p['mb']} МБ" for p in data["points"]]
    values = [p["mbits"] for p in data["points"]]
    bars = ax.bar(labels, values, color=PALETTE["accent"], width=0.5)
    ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9, color=PALETTE["ink"])
    ax.axhline(data["best_mbits"], color=PALETTE["warm"], linewidth=1.2, linestyle="--")
    ax.text(0.01, data["best_mbits"], f"рабочая полоса {data['best_mbits']:.1f} Мбит/с",
            transform=ax.get_yaxis_transform(), ha="left", va="bottom",
            fontsize=8.5, color=PALETTE["warm"])
    ax.set_ylabel("Мбит/с")
    ax.set_ylim(0, max(values) * 1.35 or 1)
    _style(ax, "Канал между машинами", "мелкие блоки медленнее: плата за раскачку")


def plot_verdicts(ax, report):
    data = report["bandwidth"]["verdicts"]
    labels = [v["label"] for v in data]
    values = [max(v["seconds"], 1e-4) for v in data]
    colors = [PALETTE["good"] if v["ok"] else PALETTE["warm"] for v in data]
    bars = ax.barh(labels, values, color=colors, height=0.5)
    ax.bar_label(bars, labels=[f"{_span(v['seconds'])} {v['unit']}" for v in data],
                 padding=4, fontsize=8.5, color=PALETTE["ink"])
    ax.set_xscale("log")
    ax.set_xlim(right=max(values) * 60)
    ax.set_xlabel("секунд на одну передачу (лог)")
    _style(ax, "Что на этом канале поедет",
           f"конвейер: потолок {report['bandwidth']['tokens_per_second']:.0f} токенов/с")
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=PALETTE["grid"], linewidth=0.8)


def plot_gpu(ax, report):
    data = report["gpu"]
    labels = [c["host"][:16] for c in data["cards"]] + ["СУММА"]
    values = [c["tflops"] for c in data["cards"]] + [data["total"]]
    colors = [PALETTE["accent"]] * len(data["cards"]) + [PALETTE["warm"]]
    bars = ax.bar(labels, values, color=colors, width=0.5)
    ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9, color=PALETTE["ink"])
    ax.set_ylabel("TFLOPS, fp16")
    ax.set_ylim(0, max(values) * 1.25 or 1)
    alive = f"живых карт {data['cards_alive']} из {data['cards_seen']}"
    _style(ax, "Карты считают одновременно",
           f"matmul 8192x8192, стена {data['wall']:.1f} с — {alive}")


def plot_pipeline(ax, report):
    data = report["pipeline"]
    labels = [f"проход {i + 1}" for i in range(len(data["times"]))]
    values = data["times"]
    bars = ax.bar(labels, values, color=PALETTE["accent"], width=0.5)
    ax.bar_label(bars, fmt="%.2f с", padding=3, fontsize=9, color=PALETTE["ink"])
    ax.set_ylabel("секунд на проход")
    ax.set_ylim(0, max(values) * 1.3 or 1)
    _style(ax, f"Конвейер: {data['layers']} слоёв на двух картах",
           f"через сеть {data['hop_kb']:.0f} КБ за проход")


def panels_for(report):
    """Какие панели вообще есть — пропущенные разделы не рисуются."""
    panels = []
    if report.get("speed"):
        panels.append(("skorost", plot_speed))
    if report.get("placement"):
        panels.append(("raskladka", plot_placement))
    if report.get("splitting"):
        panels.append(("delenie", plot_splitting))
    if report.get("bandwidth"):
        panels += [("polosa", plot_bandwidth), ("verdikt", plot_verdicts)]
    if report.get("gpu"):
        panels.append(("gpu", plot_gpu))
    if report.get("pipeline"):
        panels.append(("konveyer", plot_pipeline))
    return panels


def save_report(report, outdir):
    """Все замеры одним json — чтобы считать по ним что угодно потом."""
    path = outdir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    return path


def draw_charts(report, outdir):
    """Сохранить графики рядом с этим файлом."""
    try:
        import matplotlib
        matplotlib.use("Agg")          # без окна: скрипт может идти по ssh
        import matplotlib.pyplot as plt
    except ImportError:
        print("    matplotlib не установлен у клиента — графики пропущены")
        print("    поставьте локально: pip install matplotlib (или uv pip install)")
        return

    panels = panels_for(report)
    for order, (name, draw) in enumerate(panels, start=1):
        figure, ax = plt.subplots(figsize=(6.5, 4), dpi=160)
        draw(ax, report)
        figure.tight_layout()
        path = outdir / f"{order:02d}-{name}.png"
        figure.savefig(path, facecolor="white")
        plt.close(figure)
        print(f"    {path}")

    # Общая сводка одним листом — то, что показывают, а не листают.
    columns = 2
    rows = (len(panels) + columns) // columns          # +1 клетка под текст
    figure, axes = plt.subplots(rows, columns, figsize=(13, 4.2 * rows), dpi=140)
    flat = axes.ravel()
    for ax, (name, draw) in zip(flat, panels):
        draw(ax, report)
    summary = flat[len(panels)]
    summary.axis("off")
    summary.text(0, 1, summary_text(report), va="top", ha="left", fontsize=11,
                 family="monospace", color=PALETTE["ink"], linespacing=1.7)
    for ax in flat[len(panels) + 1:]:
        ax.axis("off")
    figure.suptitle("LOOMA FLOAT — кластер из домашних машин", fontsize=15,
                    fontweight="bold", color=PALETTE["ink"], x=0.02, ha="left")
    figure.tight_layout(rect=(0, 0, 1, 0.97))
    path = outdir / "00-svodka.png"
    figure.savefig(path, facecolor="white")
    plt.close(figure)
    print(f"    {path}   <- сводка одним листом")


def summary_text(report):
    total = report.get("total", {})
    lines = [
        f"машин          {len(report.get('nodes', []))}",
        f"ядер           {total.get('CPU', 0):.0f}",
        f"карт           {total.get('GPU', 0):.0f}",
        f"памяти         {total.get('memory', 0) / 1024 ** 3:.0f} ГБ",
    ]
    if report.get("speed"):
        rates = [s["rate"] for s in report["speed"]]
        lines.append(f"разрыв машин   {max(rates) / min(rates):.1f}x")
    if report.get("splitting"):
        split = report["splitting"]
        lines.append(f"выигрыш        {split['speedup']:.2f}x из {split['ceiling']:.2f}x")
    if report.get("bandwidth"):
        band = report["bandwidth"]
        lines.append(f"канал          {band['best_mbits']:.1f} Мбит/с")
        lines.append(f"потолок ток/с  {band['tokens_per_second']:.0f}")
    if report.get("gpu"):
        gpu = report["gpu"]
        lines.append(f"на картах      {gpu['total']:.1f} TFLOPS "
                     f"({gpu['cards_alive']} из {gpu['cards_seen']})")
    lines += ["", "ни одного публичного адреса", "ни одного проброшенного порта"]
    return "\n".join(lines)


def connect():
    """Подключиться. Если узлы не тянут RUNTIME_ENV — сказать и войти без него.

    Отказ приходит именно на ray.init: плагин pip у Ray собирает окружение
    задания ещё до того, как отдаст клиенту соединение. Ронять из-за этого весь
    прогон незачем — остальные разделы от runtime_env не зависят.
    """
    print(f"\n  LOOMA FLOAT — демонстрация кластера\n  подключаюсь к {ADDRESS}")
    if not RUNTIME_ENV:
        ray.init(ADDRESS, log_to_driver=False)
        return ""

    print(f"  окружение задания: {RUNTIME_ENV}")
    try:
        ray.init(ADDRESS, runtime_env=RUNTIME_ENV, log_to_driver=False)
        return ""
    except ConnectionAbortedError as error:
        if "virtualenv" not in str(error):
            raise
        try:
            ray.shutdown()
        except Exception:
            pass
        print("\n  ! узлы этого кластера не умеют runtime_env: у них нет пакета")
        print("    virtualenv, через который Ray заводит окружение задания.")
        print("    Кластер поднят до того, как virtualenv попал в требования;")
        print("    новый кластер получит его сам. Подключаюсь без runtime_env.")
        ray.init(ADDRESS, log_to_driver=False)
        return "на узлах нет virtualenv"


def main():
    fallback = connect()
    report = {"address": ADDRESS, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
              "runtime_env": RUNTIME_ENV or None, "runtime_env_fallback": fallback}
    try:
        nodes, total = inventory(report)
        if len(nodes) < 2:
            raise SystemExit("\n  в кластере меньше двух узлов — показывать нечего")

        speeds = calibrate(nodes, report)
        placement(nodes, speeds, report)
        splitting(nodes, speeds, report)
        bandwidth(nodes, report)
        tflops, ready = gpus(nodes, report)
        if tflops:
            pipeline(ready, report)
        resilience(nodes, report)
        libraries(nodes, fallback)

        title("ИТОГ")
        for line in summary_text(report).splitlines():
            print(f"    {line}")
        print("\n  Клиентский код — обычный Ray, без единой строчки про сеть.")

        title("ЧТО СОХРАНЕНО")
        outdir = Path(__file__).resolve().parent / "charts"
        outdir.mkdir(exist_ok=True)
        report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        draw_charts(report, outdir)
        print(f"    {save_report(report, outdir)}   <- все замеры числами")
        print()
    finally:
        ray.shutdown()


if __name__ == "__main__":
    main()
