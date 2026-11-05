"""Segunda opinión con un modelo de lenguaje (API de Anthropic) para los casos que las reglas no deciden.

Alómetro es determinista: cuando no encuentra evidencia suficiente marca la
resolución como ``incierto``. Solo esos casos se envían a un modelo de lenguaje,
con tres salvaguardas:

1. Solo se envía texto que ya pasó por la anonimización y la compuerta.
2. Antes de cada llamada se repite la compuerta sobre lo que se va a enviar; si
   encuentra algo, la conversación NO se envía.
3. La respuesta es JSON con esquema fijo (structured outputs) y debe citar el turno
   que la respalda, igual que Alómetro.

El acuerdo entre esta segunda opinión y los analistas humanos se mide con
``alometro calibrar`` (kappa de Cohen) antes de usarla en una operación real.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from alometro.modelos import Conversacion, Evaluacion

from .gate import revisar_conversacion

MODELO = os.environ.get("SAFELISTEN_MODELO_LLM", "claude-opus-5-5")

SISTEMA = (
    "Eres analista de calidad de un call center en Colombia. Recibes una conversación ya "
    "anonimizada (los datos personales aparecen como [CEDULA], [NOMBRE], etc.). Decide si la "
    "solicitud del cliente quedó resuelta en el contacto, quedó en trámite formal (con radicado "
    "o plazo concreto) o no quedó resuelta. Basa la decisión solo en lo que dice la conversación "
    "y cita el número de turno que mejor la respalda. Si la evidencia no alcanza, dilo en la "
    "justificación y elige la opción más conservadora (no_resuelto)."
)

ESQUEMA = {
    "type": "object",
    "properties": {
        "resolucion": {"type": "string", "enum": ["resuelto", "en_tramite", "no_resuelto"]},
        "turno_evidencia": {"type": "integer"},
        "justificacion": {"type": "string"},
    },
    "required": ["resolucion", "turno_evidencia", "justificacion"],
    "additionalProperties": False,
}


class EnvioBloqueado(Exception):
    """La conversación aún tiene posibles datos personales: no se envía al modelo."""


@dataclass
class Revision:
    conversacion_id: str
    modelo: str
    resolucion_reglas: str
    resolucion_llm: str | None
    turno_evidencia: int | None
    justificacion: str
    estado: str  # "ok" | "rechazada_por_modelo" | "bloqueada_por_privacidad"

    def fila(self, lote_id: str) -> dict:
        return {"lote_id": lote_id, **self.__dict__}


def transcripcion(conv: Conversacion) -> str:
    return "\n".join(f"[{i}] {t.rol}: {t.texto}" for i, t in enumerate(conv.turnos))


def necesita_revision(ev: Evaluacion) -> bool:
    return ev.senales.get("resolucion") == "incierto"


def _cliente_por_defecto():  # pragma: no cover - requiere credenciales
    import anthropic

    return anthropic.Anthropic()


def revisar(conv: Conversacion, ev: Evaluacion, cliente=None) -> Revision:
    base = {"conversacion_id": conv.id, "modelo": MODELO, "resolucion_reglas": ev.senales.get("resolucion")}
    if revisar_conversacion(conv):
        return Revision(
            **base,
            resolucion_llm=None,
            turno_evidencia=None,
            justificacion="No enviada: la compuerta encontró posibles datos personales.",
            estado="bloqueada_por_privacidad",
        )
    cliente = cliente or _cliente_por_defecto()
    respuesta = cliente.beta.messages.create(
        model=MODELO,
        max_tokens=4000,
        system=SISTEMA,
        messages=[{"role": "user", "content": transcripcion(conv)}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": ESQUEMA}},
        # Si el modelo declina por política, la API reintenta con el modelo de respaldo recomendado.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if respuesta.stop_reason == "refusal":
        return Revision(
            **base,
            resolucion_llm=None,
            turno_evidencia=None,
            justificacion="El modelo declinó la solicitud.",
            estado="rechazada_por_modelo",
        )
    texto = next(b.text for b in respuesta.content if b.type == "text")
    datos = json.loads(texto)
    return Revision(
        **base,
        resolucion_llm=datos["resolucion"],
        turno_evidencia=datos["turno_evidencia"],
        justificacion=datos["justificacion"],
        estado="ok",
    )
