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


def test_manifiesto_que_no_es_objeto_422(cliente, archivos):
    archivos["manifiesto"] = ("m.json", b"[]")
    r = cliente.post("/v1/lotes", files=archivos, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 422


def test_reusa_un_solo_warehouse(tmp_path, archivos):
    creados = []

    def fabrica():
        creados.append(1)
        return DuckDBWarehouse(tmp_path / "uno.duckdb")

    c = TestClient(crear_app(fabrica, token=TOKEN))
    for _ in range(2):
        assert c.post("/v1/lotes", files=archivos, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert len(creados) == 1


def test_enviar_lote_contra_servidor_real(tmp_path, convs_despues):
    """El cliente de la CLI sube un lote a la API corriendo de verdad (uvicorn en un hilo)."""
    import socket
    import threading
    import time

    uvicorn = pytest.importorskip("uvicorn")
    from safelisten.cli import enviar_lote

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        puerto = s.getsockname()[1]
    app = crear_app(lambda: DuckDBWarehouse(tmp_path / "srv.duckdb"), token=TOKEN)
    servidor = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=puerto, log_level="error"))
    hilo = threading.Thread(target=servidor.run, daemon=True)
    hilo.start()
    try:
        for _ in range(100):
            if servidor.started:
                break
            time.sleep(0.05)
        exp = exportar_conversaciones(convs_despues[:10], tmp_path / "lote", SAL, "Cliente HTTP")
        respuesta = enviar_lote(exp.directorio, f"http://127.0.0.1:{puerto}", TOKEN)
        assert respuesta["conversaciones"] == 10
    finally:
        servidor.should_exit = True
        hilo.join(timeout=5)
