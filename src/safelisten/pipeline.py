"""Los dos pasos del flujo, sin depender de un orquestador.

* ``exportar``: corre en la empresa. Carga, anonimiza, pasa la compuerta y escribe el
  lote (``conversaciones.jsonl`` + ``manifiesto.json``).
* ``procesar``: corre en la nube. Verifica el manifiesto, repite la compuerta,
  califica con Alómetro y carga el warehouse.

``flows.py`` envuelve estas funciones en Prefect para programarlas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alometro import calcular_indicadores
from alometro.cargar import a_jsonl, cargar, desde_jsonl
from alometro.modelos import Conversacion, Evaluacion

from . import manifest as mf
from .edge import anonimizar_lote
from .gate import compuerta
from .score import calificar, fila_lote
from .warehouse import Warehouse


class LoteRechazado(Exception):
    """El lote no se procesa: manifiesto inválido o la compuerta encontró datos personales."""


@dataclass
class ResultadoExportacion:
    directorio: Path
    manifiesto: dict
    en_cuarentena: list[str]


@dataclass
class ResultadoProceso:
    lote_id: str
    conversaciones: int
    evaluaciones: list[Evaluacion]
    resumen: dict

    def a_dict(self) -> dict:
        inds = {
            i.id: {"nombre": i.nombre, "valor": i.valor, "meta": i.meta, "unidad": i.unidad, "estado": i.estado}
            for i in self.resumen["indicadores"]
        }
        return {
            "lote_id": self.lote_id,
            "conversaciones": self.conversaciones,
            "indicadores": inds,
            "contencion": self.resumen["contencion"],
        }


def cargar_entradas(rutas: list[str | Path]) -> list[Conversacion]:
    convs: list[Conversacion] = []
    for ruta in rutas:
        convs.extend(cargar(ruta))
    return convs


def exportar_conversaciones(
    convs: list[Conversacion], salida: str | Path, sal: bytes, organizacion: str, lote_id: str | None = None
) -> ResultadoExportacion:
    anonimas, stats = anonimizar_lote(convs, sal)
    resultado = compuerta(anonimas)
    datos = a_jsonl(resultado.aprobadas).encode("utf-8")
    manifiesto = mf.construir(datos, stats.a_dict(), resultado.resumen(), organizacion, lote_id)
    directorio = Path(salida)
    mf.guardar(directorio, datos, manifiesto)
    if resultado.en_cuarentena:
        # Solo los identificadores: el texto original nunca sale de la máquina.
        (directorio / "cuarentena.txt").write_text("\n".join(resultado.en_cuarentena) + "\n", encoding="utf-8")
    return ResultadoExportacion(directorio, manifiesto, resultado.en_cuarentena)


def exportar(rutas: list[str | Path], salida: str | Path, sal: bytes, organizacion: str) -> ResultadoExportacion:
    return exportar_conversaciones(cargar_entradas(rutas), salida, sal, organizacion)


def leer_lote(manifiesto: dict, datos: bytes) -> list[Conversacion]:
    """Verifica el manifiesto y repite la compuerta del lado de la nube."""
    try:
        mf.verificar(manifiesto, datos)
    except mf.ManifiestoInvalido as e:
        raise LoteRechazado(str(e)) from e
    convs = desde_jsonl(datos.decode("utf-8"))
    revision = compuerta(convs)
    if not revision.limpio:
        raise LoteRechazado(
            f"La compuerta de la nube encontró posibles datos personales: {revision.resumen()['hallazgos_por_tipo']}. "
            "Lote rechazado completo; no se guardó nada."
        )
    return convs


def procesar(manifiesto: dict, datos: bytes, warehouse: Warehouse) -> ResultadoProceso:
    convs = leer_lote(manifiesto, datos)
    lote_id = manifiesto["lote_id"]
    evaluaciones, filas = calificar(lote_id, convs)
    recibido = datetime.now(timezone.utc).isoformat(timespec="seconds")
    warehouse.cargar("raw_lotes", lote_id, [fila_lote(manifiesto, recibido)])
    for tabla, filas_tabla in filas.items():
        warehouse.cargar(tabla, lote_id, filas_tabla)
    return ResultadoProceso(lote_id, len(convs), evaluaciones, calcular_indicadores(evaluaciones))


def procesar_directorio(directorio: str | Path, warehouse: Warehouse) -> ResultadoProceso:
    manifiesto, datos = mf.leer(Path(directorio))
    return procesar(manifiesto, datos, warehouse)
