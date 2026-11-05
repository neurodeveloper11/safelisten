"""Orquestación con Prefect (opcional: ``pip install prefect``).

Ejemplo de despliegue programado en la empresa, cada noche a la 1:00 a. m.::

    prefect deploy src/safelisten/flows.py:flujo_nocturno --cron "0 1 * * *"

El flujo exporta lo del día (zona local) y envía el lote a la API de Cloud Run.
Las funciones de negocio viven en ``pipeline.py`` y se prueban sin Prefect.
"""

from __future__ import annotations

import os
from pathlib import Path

from prefect import flow, task

from .cli import enviar_lote
from .pipeline import exportar


@task(retries=0)
def tarea_exportar(entradas: list[str], salida: str, organizacion: str) -> str:
    sal = bytes.fromhex(os.environ["SAFELISTEN_SAL"])
    resultado = exportar(entradas, salida, sal, organizacion)
    return str(resultado.directorio)


@task(retries=3, retry_delay_seconds=[10, 60, 300])
def tarea_enviar(directorio: str, url: str) -> dict:
    return enviar_lote(Path(directorio), url, os.environ["SAFELISTEN_TOKEN"])


@flow(name="safelisten-nocturno")
def flujo_nocturno(entradas: list[str], salida: str, organizacion: str, url: str) -> dict:
    directorio = tarea_exportar(entradas, salida, organizacion)
    return tarea_enviar(directorio, url)
