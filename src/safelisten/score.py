"""Zona nube: califica con Alómetro las conversaciones ya anonimizadas y las aplana en filas.

Las filas van a tres tablas crudas (``raw_*``) del warehouse. dbt las transforma
después en indicadores, tableros de cumplimiento y planes de coaching.
"""

from __future__ import annotations

from datetime import timezone

from alometro import evaluar
from alometro.modelos import Conversacion, Evaluacion

# Esquemas portables: tipos que existen en DuckDB y en BigQuery.
# Las fechas viajan como texto ISO en UTC y dbt las convierte (cast as timestamp).
ESQUEMAS: dict[str, list[tuple[str, str]]] = {
    "raw_lotes": [
        ("lote_id", "STRING"),
        ("organizacion", "STRING"),
        ("creado_en", "STRING"),
        ("recibido_en", "STRING"),
        ("conversaciones", "INT64"),
        ("reemplazos_pii", "INT64"),
        ("en_cuarentena", "INT64"),
        ("sha256", "STRING"),
        ("version_datashield", "STRING"),
        ("version_alometro", "STRING"),
    ],
    "raw_evaluaciones": [
        ("lote_id", "STRING"),
        ("conversacion_id", "STRING"),
        ("tipo", "STRING"),
        ("canal", "STRING"),
        ("sector", "STRING"),
        ("direccion", "STRING"),
        ("agente_id", "STRING"),
        ("cliente_hash", "STRING"),
        ("inicio_utc", "STRING"),
        ("fecha_local", "STRING"),
        ("turnos", "INT64"),
        ("puntaje", "INT64"),
        ("puntaje_base", "INT64"),
        ("nivel", "STRING"),
        ("critico_cliente", "BOOL"),
        ("critico_negocio", "BOOL"),
        ("critico_cumplimiento", "BOOL"),
        ("resolucion", "STRING"),
        ("trayectoria_emocional", "STRING"),
        ("frustracion_final", "FLOAT64"),
        ("peticiones_humano", "INT64"),
        ("humano_atendido", "BOOL"),
        ("bucle_bot", "BOOL"),
        ("esfuerzo", "INT64"),
        ("abandono", "BOOL"),
        ("queja", "BOOL"),
        ("amenaza_externa", "BOOL"),
        ("hubo_bot", "BOOL"),
        ("hubo_agente", "BOOL"),
        ("en_ventana_legal", "BOOL"),
        ("dim_resolucion", "INT64"),
        ("dim_esfuerzo", "INT64"),
        ("dim_trato", "INT64"),
        ("dim_bot", "INT64"),
        ("dim_proceso", "INT64"),
        ("dim_cumplimiento", "INT64"),
    ],
    "raw_criterios": [
        ("lote_id", "STRING"),
        ("conversacion_id", "STRING"),
        ("criterio_id", "STRING"),
        ("nombre", "STRING"),
        ("dimension", "STRING"),
        ("critico", "STRING"),
        ("aplica", "BOOL"),
        ("cumple", "BOOL"),
    ],
    "raw_revisiones_llm": [
        ("lote_id", "STRING"),
        ("conversacion_id", "STRING"),
        ("modelo", "STRING"),
        ("resolucion_reglas", "STRING"),
        ("resolucion_llm", "STRING"),
        ("turno_evidencia", "INT64"),
        ("justificacion", "STRING"),
        ("estado", "STRING"),
    ],
}


def _hora_utc(conv: Conversacion) -> tuple[str | None, str | None]:
    if not conv.inicio:
        return None, None
    local = conv.inicio
    utc = local.astimezone(timezone.utc) if local.tzinfo else local
    return utc.strftime("%Y-%m-%d %H:%M:%S"), local.strftime("%Y-%m-%d")


def fila_evaluacion(lote_id: str, conv: Conversacion, ev: Evaluacion) -> dict:
    s = ev.senales
    dims = ev.dimensiones
    categorias = {e.get("categoria") for e in ev.errores_criticos}
    inicio_utc, fecha_local = _hora_utc(conv)
    ventana = s.get("ventana_legal") or {}
    return {
        "lote_id": lote_id,
        "conversacion_id": conv.id,
        "tipo": ev.tipo,
        "canal": ev.canal,
        "sector": ev.sector,
        "direccion": ev.direccion,
        "agente_id": ev.agente_id,
        "cliente_hash": conv.cliente_id,
        "inicio_utc": inicio_utc,
        "fecha_local": fecha_local,
        "turnos": len(conv.turnos),
        "puntaje": ev.puntaje,
        "puntaje_base": ev.puntaje_base,
        "nivel": ev.nivel,
        "critico_cliente": "cliente" in categorias,
        "critico_negocio": "negocio" in categorias,
        "critico_cumplimiento": "cumplimiento" in categorias,
        "resolucion": s.get("resolucion"),
        "trayectoria_emocional": s.get("trayectoria_emocional"),
        "frustracion_final": s.get("frustracion_final"),
        "peticiones_humano": s.get("peticiones_humano"),
        "humano_atendido": s.get("humano_atendido"),
        "bucle_bot": s.get("bucle_bot"),
        "esfuerzo": s.get("esfuerzo"),
        "abandono": s.get("abandono"),
        "queja": s.get("queja"),
        "amenaza_externa": s.get("amenaza_externa"),
        "hubo_bot": s.get("hubo_bot"),
        "hubo_agente": s.get("hubo_agente"),
        "en_ventana_legal": ventana.get("permitido"),
        **{f"dim_{k}": dims.get(k) for k in ("resolucion", "esfuerzo", "trato", "bot", "proceso", "cumplimiento")},
    }


def filas_criterios(lote_id: str, ev: Evaluacion) -> list[dict]:
    return [
        {
            "lote_id": lote_id,
            "conversacion_id": ev.conversacion_id,
            "criterio_id": c.id,
            "nombre": c.nombre,
            "dimension": c.dimension,
            "critico": c.critico,
            "aplica": c.aplica,
            "cumple": c.cumple,
        }
        for c in ev.criterios
    ]


def fila_lote(manifiesto: dict, recibido_en: str) -> dict:
    priv = manifiesto.get("privacidad", {})
    soft = manifiesto.get("software", {})
    return {
        "lote_id": manifiesto["lote_id"],
        "organizacion": manifiesto.get("organizacion"),
        "creado_en": manifiesto.get("creado_en"),
        "recibido_en": recibido_en,
        "conversaciones": manifiesto.get("compuerta", {}).get("aprobadas"),
        "reemplazos_pii": priv.get("reemplazos_total"),
        "en_cuarentena": manifiesto.get("compuerta", {}).get("en_cuarentena"),
        "sha256": manifiesto.get("archivo", {}).get("sha256"),
        "version_datashield": soft.get("datashield-es"),
        "version_alometro": soft.get("alometro"),
    }


def calificar(lote_id: str, convs: list[Conversacion]) -> tuple[list[Evaluacion], dict[str, list[dict]]]:
    evaluaciones = [evaluar(c) for c in convs]
    filas = {
        "raw_evaluaciones": [fila_evaluacion(lote_id, c, e) for c, e in zip(convs, evaluaciones)],
        "raw_criterios": [f for e in evaluaciones for f in filas_criterios(lote_id, e)],
    }
    return evaluaciones, filas
