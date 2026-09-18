"""`/admin/train`: от формы до группы задач и обратно к результату.

Стенд настоящий (оркестратор + агент), модель — заглушка `describe`: до
HuggingFace тесты не ходят, а обучение на стенде не идёт — команда стадии
заменяется на скрипт, который пишет `progress.json` так, как это сделала бы
голова.
"""

from __future__ import annotations

import base64
import json
import sys
import time

import pytest

from looma.orchestrator.models import ModelInfo, training_stage_bytes
from test_agent_gateway import ADMIN_HEADERS, stand  # noqa: F401
from test_model_deploy import api


def qwen_like(**extra) -> ModelInfo:
    return ModelInfo(repo="Qwen/Qwen3-8B", num_layers=36, hidden_size=4096,
                     architecture="Qwen3ForCausalLM", intermediate_size=12288,
                     num_attention_heads=32, num_key_value_heads=8,
                     vocab_size=152064, **extra)


DATASET = base64.b64encode(b'{"messages": [{"role": "user", "content": "a"}, '
                           b'{"role": "assistant", "content": "b"}]}\n').decode()


def body(**extra) -> dict:
    return {"repo": "Qwen/Qwen3-8B", "dataset": DATASET, "precision": "bf16",
            "schedule": {"epochs": 1, "batch_size": 2, "micro_size": 1, "lr": 1e-4},
            "lora": {"r": 8}, "max_len": 64, **extra}


# ------------------------------------------------------------ оценка памяти
def test_оценка_памяти_считается_от_параметров():
    """Веса среза — по числу параметров слоя и точности, а не по размеру
    файлов: у квантованного чекпоинта файлы втрое меньше памяти."""
    model = qwen_like()
    assert 8.0e9 < model.params < 8.4e9
    bf16 = training_stage_bytes(model, layers=36, is_first=True, is_last=True,
                                precision="bf16", lora_r=16, micro_tokens=2048,
                                micros_in_flight=4)
    nf4 = training_stage_bytes(model, layers=36, is_first=True, is_last=True,
                               precision="nf4", lora_r=16, micro_tokens=2048,
                               micros_in_flight=4)
    assert bf16["weights"] > 15 * 2**30
    assert nf4["weights"] < bf16["weights"] / 2.5
    assert nf4["total"] < bf16["total"]
    # Логиты только у последней стадии.
    middle = training_stage_bytes(model, layers=12, is_first=False, is_last=False,
                                  precision="bf16", lora_r=16, micro_tokens=2048,
                                  micros_in_flight=4)
    assert middle["logits"] == 0 and bf16["logits"] > 0


# ------------------------------------------------------------------ отказы
def test_квантованную_модель_учить_нельзя(stand, monkeypatch):
    orchestrator, _agent = stand
    monkeypatch.setattr("looma.api.app.describe",
                        lambda repo, **kw: qwen_like(quantization="mxfp4"))
    answer = api(orchestrator).post("/admin/train", json=body())
    assert answer.status_code == 400 and "квантован" in answer.json()["error"]["message"]


def test_без_датасета_отказ(stand, monkeypatch):
    orchestrator, _agent = stand
    monkeypatch.setattr("looma.api.app.describe", lambda repo, **kw: qwen_like())
    answer = api(orchestrator).post("/admin/train", json=body(dataset=""))
    assert answer.status_code == 400 and "датасет" in answer.json()["error"]["message"]


def test_не_влезает_отказ_с_подсказкой(stand, monkeypatch):
    """До запуска, по каждой стадии, с числами и советом: у тестового узла
    памяти нет вовсе, а 8B в bf16 просит ~17 ГБ."""
    orchestrator, _agent = stand
    monkeypatch.setattr("looma.api.app.describe", lambda repo, **kw: qwen_like())
    answer = api(orchestrator).post("/admin/train", json=body())
    assert answer.status_code == 409
    message = answer.json()["error"]["message"]
    assert "не влезает" in message and "nf4" in message


# ------------------------------------------------------------- полный круг
def test_обучение_запускается_и_доходит_до_результата(stand, monkeypatch, tmp_path):
    """Форма → группа с engine=train, train.json и датасетом во входах →
    прогресс из результатов головы → запись закрыта по её исходу."""
    orchestrator, _agent = stand
    monkeypatch.setattr("looma.api.app.describe", lambda repo, **kw: qwen_like())
    client = api(orchestrator)

    # Вместо стадии — скрипт, который делает то, что сделала бы голова в
    # конце: пишет progress.json и адаптер в out/.
    original = orchestrator.hub.submit_group
    seen = {}

    def fake_stage(**kwargs):
        seen.update(kwargs)
        script = (
            "import json, os, pathlib;"
            "cfg = json.load(open('train.json'));"
            "rows = open('train.jsonl').read().splitlines();"
            "out = pathlib.Path(os.environ['LOOMA_TASK_OUT']);"
            "(out / 'adapter').mkdir();"
            "(out / 'adapter' / 'adapter_config.json').write_text('{}');"
            "(out / 'adapter' / 'adapter_model.safetensors').write_bytes(b'x');"
            "result = {'adapter': 'adapter', 'steps': 1, 'rows': len(rows), 'base_model': cfg['base_model']};"
            "(out / 'result.json').write_text(json.dumps(result));"
            "(out / 'progress.json').write_text(json.dumps({'state': 'done', 'step': 1, 'total_steps': 1, 'history': [], 'result': result}))"
        )
        kwargs["per_rank"] = [{"command": [sys.executable, "-c", script]}]
        kwargs["command"] = kwargs["per_rank"][0]["command"]
        kwargs["environment"] = None
        return original(**kwargs)

    monkeypatch.setattr(orchestrator.hub, "submit_group", fake_stage)
    answer = client.post("/admin/train", json=body(force=True, label="my-lora"))
    assert answer.status_code == 200, answer.text
    made = answer.json()
    group_id = made["group_id"]
    assert made["state"] == "running" and made["label"] == "my-lora"
    assert made["plan"][0]["start_layer"] == 0 and made["plan"][0]["end_layer"] == 36
    # Что уехало стадии: движок, конфиг и датасет во входах.
    assert "train.json" in seen["inputs"] and "train.jsonl" in seen["inputs"]
    config = json.loads(seen["inputs"]["train.json"])
    assert config["precision"] == "bf16" and config["lora"]["r"] == 8
    assert config["base_model"] == "Qwen/Qwen3-8B"
    # В запись не кладём сам датасет — только его размер.
    assert "dataset" not in made["request"] and made["request"]["dataset_bytes"] > 0

    deadline = time.time() + 60
    view = None
    while time.time() < deadline:
        view = client.get(f"/admin/train/{group_id}").json()
        if view["state"] != "running":
            break
        time.sleep(0.5)
    assert view and view["state"] == "done", json.dumps(view, ensure_ascii=False)[:3000]
    assert view["result"]["rows"] == 1
    assert view["files"]["adapter_model.safetensors"].endswith("/results/adapter/adapter_model.safetensors")
    listed = client.get("/admin/train").json()["jobs"]
    assert [j["group_id"] for j in listed] == [group_id]

    # Адаптер скачивается тем же путём, что любой результат задачи.
    got = client.get(view["files"]["adapter_model.safetensors"])
    assert got.status_code == 200 and got.content == b"x"


def test_остановка_закрывает_запись(stand, monkeypatch):
    orchestrator, _agent = stand
    monkeypatch.setattr("looma.api.app.describe", lambda repo, **kw: qwen_like())
    client = api(orchestrator)
    original = orchestrator.hub.submit_group

    def sleeper(**kwargs):
        kwargs["per_rank"] = [{"command": [sys.executable, "-c", "import time; time.sleep(60)"]}]
        kwargs["command"] = kwargs["per_rank"][0]["command"]
        kwargs["environment"] = None
        return original(**kwargs)

    monkeypatch.setattr(orchestrator.hub, "submit_group", sleeper)
    group_id = client.post("/admin/train", json=body(force=True)).json()["group_id"]
    stopped = client.post(f"/admin/train/{group_id}/stop").json()
    assert stopped["state"] == "stopped"
    assert client.get(f"/admin/train/{group_id}").json()["state"] == "stopped"
