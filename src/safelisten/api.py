"""Servicio HTTP para Cloud Run: recibe lotes ya anonimizados y los procesa.

POST /v1/lotes   multipart con ``manifiesto`` (JSON) y ``conversaciones`` (JSONL).
GET  /salud      verificación de vida para Cloud Run.

Autenticación: token Bearer en la variable ``SAFELISTEN_TOKEN`` (en GCP, desde
Secret Manager). Si la variable no existe el servicio se niega a arrancar, salvo
que se active explícitamente ``SAFELISTEN_MODO_DESARROLLO=1``.
"""

from __future__ import annotations

import hmac
import json
import os
from collections.abc import Callable

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile

from . import __version__
from .pipeline import LoteRechazado, procesar
from .warehouse import BigQueryWarehouse, DuckDBWarehouse, Warehouse

MAX_BYTES = 50 * 1024 * 1024


def warehouse_desde_entorno() -> Warehouse:
    proyecto = os.environ.get("SAFELISTEN_BQ_PROYECTO")
    dataset = os.environ.get("SAFELISTEN_BQ_DATASET")
    if proyecto and dataset:  # pragma: no cover - requiere GCP
        return BigQueryWarehouse(proyecto, dataset)
    return DuckDBWarehouse(os.environ.get("SAFELISTEN_DUCKDB", "/tmp/safelisten.duckdb"))


def crear_app(warehouse: Callable[[], Warehouse] = warehouse_desde_entorno, token: str | None = None) -> FastAPI:
    token = token if token is not None else os.environ.get("SAFELISTEN_TOKEN")
    if not token and os.environ.get("SAFELISTEN_MODO_DESARROLLO") != "1":
        raise RuntimeError("Falta SAFELISTEN_TOKEN (o SAFELISTEN_MODO_DESARROLLO=1 para pruebas locales).")

    app = FastAPI(title="SafeListen", version=__version__)

    def autorizar(authorization: str | None = Header(default=None)) -> None:
        if not token:
            return
        recibido = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(recibido.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="Token inválido.")

    @app.get("/salud")
    def salud() -> dict:
        return {"estado": "ok", "version": __version__}

    @app.post("/v1/lotes", dependencies=[Depends(autorizar)])
    async def recibir_lote(manifiesto: UploadFile = File(...), conversaciones: UploadFile = File(...)) -> dict:
        datos = await conversaciones.read(MAX_BYTES + 1)
        if len(datos) > MAX_BYTES:
            raise HTTPException(status_code=413, detail="Lote demasiado grande (máx. 50 MB). Divídalo.")
        try:
            meta = json.loads(await manifiesto.read())
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=422, detail="El manifiesto no es JSON válido.") from e
        try:
            resultado = procesar(meta, datos, warehouse())
        except LoteRechazado as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return resultado.a_dict()

    return app
