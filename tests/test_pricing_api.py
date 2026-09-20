"""Публичный прайс: открыт без входа, пишется администратором, скрытые модели
наружу не уходят."""
from fastapi.testclient import TestClient

from looma.api.app import create_app
from looma.orchestrator.config import OrchestratorConfig


def client(tmp_path):
    config = OrchestratorConfig(data_dir=str(tmp_path), admin_token="secret")
    return TestClient(create_app(config=config))


def test_public_pricing_is_open_and_hides_hidden_models(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/public/pricing").status_code == 200
    doc = {
        "gpu_classes": [{"id": "4090", "name": "RTX 4090", "vram_gb": 24, "rate_kopecks": 12000,
                         "competitors": {"selectel": 31000, "aws": ""}, "match": ["4090"]}],
        "models": [{"id": "Qwen3-32B", "context": 32768, "price_in": 2000, "price_out": 8000},
                   {"id": "secret", "price_in": 1, "price_out": 1, "visible": False}],
    }
    assert c.put("/admin/pricing", json=doc).status_code in (401, 403)
    put = c.put("/admin/pricing", json=doc, headers={"X-Looma-Admin-Token": "secret"})
    assert put.status_code == 200, put.text
    assert put.json()["as_of"]
    public = c.get("/api/public/pricing").json()
    assert [m["id"] for m in public["models"]] == ["Qwen3-32B"]
    assert public["gpu_classes"][0]["competitors"] == {"selectel": 31000}
    # переживает перезапуск: новый экземпляр читает тот же файл
    again = client(tmp_path).get("/admin/pricing", headers={"X-Looma-Admin-Token": "secret"}).json()
    assert again["gpu_classes"][0]["rate_kopecks"] == 12000


def test_client_rates_need_sign_in(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/rates").status_code == 401
    ok = c.get("/api/rates", headers={"X-Looma-Admin-Token": "secret"})
    assert ok.status_code == 200 and "gpu_classes" in ok.json()
