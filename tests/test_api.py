import json

import pytest
from conftest import SAL
from fastapi.testclient import TestClient

from safelisten import manifest as mf
from safelisten.api import crear_app
from safelisten.pipeline import exportar_conversaciones
from safelisten.warehouse import DuckDBWarehouse

TOKEN = "token-de-prueba"


@pytest.fixture
def cliente(tmp_path):
    ruta = tmp_path / "api.duckdb"
    return TestClient(crear_app(lambda: DuckDBWarehouse(ruta), token=TOKEN))


@pytest.fixture
def archivos(tmp_path, convs_despues):
    exp = exportar_conversaciones(convs_despues[:40], tmp_path / "lote", SAL, "API")
    manifiesto, datos = mf.leer(exp.directorio)
    return {"manifiesto": ("m.json", json.dumps(manifiesto).encode()), "conversaciones": ("c.jsonl", datos)}


def test_salud(cliente):
    assert cliente.get("/salud").json()["estado"] == "ok"


def test_sin_token_401(cliente, archivos):
    assert cliente.post("/v1/lotes", files=archivos).status_code == 401
    assert cliente.post("/v1/lotes", files=archivos, headers={"Authorization": "Bearer otro"}).status_code == 401


def test_lote_valido(cliente, archivos):
    r = cliente.post("/v1/lotes", files=archivos, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert cuerpo["conversaciones"] == 40
    assert "I16" in cuerpo["indicadores"]


def test_lote_alterado_422(cliente, archivos):
    archivos["conversaciones"] = ("c.jsonl", archivos["conversaciones"][1] + b"\n")
    r = cliente.post("/v1/lotes", files=archivos, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 422


def test_no_arranca_sin_token(monkeypatch):
    monkeypatch.delenv("SAFELISTEN_TOKEN", raising=False)
    monkeypatch.delenv("SAFELISTEN_MODO_DESARROLLO", raising=False)
    with pytest.raises(RuntimeError):
        crear_app(token="")
