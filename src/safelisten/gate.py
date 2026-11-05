"""Compuerta de fugas: verifica que el texto anonimizado no conserve datos personales.

Se aplican dos detectores independientes, para que un error de uno lo atrape el otro:

1. DataShield otra vez sobre el texto ya anonimizado (debe encontrar 0).
2. Un detector propio, más simple y de implementación distinta: correos, celulares
   colombianos, tarjetas válidas por Luhn, cédulas con puntos y cualquier secuencia
   de 7 o más dígitos que no sea un radicado.

La compuerta corre dos veces: en la empresa (antes de exportar) y en la nube (al
recibir el lote). Si en la nube encuentra algo, el lote completo se rechaza.

Los hallazgos nunca guardan el valor encontrado, solo su tipo y su ubicación,
para que los registros de la compuerta no se conviertan en una fuga.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from alometro.modelos import Conversacion
from datashield_es import detect

from .edge import PREFIJO_HASH, proteger_radicados

_CORREO = re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")
_CELULAR = re.compile(r"(?<!\d)(\+?57[ -]?)?3\d{2}[ -]?\d{3}[ -]?\d{4}(?!\d)")
_TARJETA = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
# Montos («$1.200.000», «$ 480.000») no son documentos: se excluyen por el signo de pesos.
_CEDULA_PUNTOS = re.compile(r"(?<![\d$])(?<!\$ )\d{1,3}(\.\d{3}){2,3}(?!\d)")
# Nombre introducido por contexto (sin diccionario, para ser independiente de la zona local).
_NOMBRE_CONTEXTO = re.compile(
    r"(?i:hablo\s+con|se[ñn]or(?:a|ita)?|do[ñn]a|me\s+llamo|mi\s+nombre\s+es)\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+"
)
_DIGITOS_LARGOS = re.compile(r"(?<![\w$.])(?<!\$ )\d{7,}(?![\w])")


def _luhn(numero: str) -> bool:
    digitos = [int(c) for c in numero if c.isdigit()]
    if not 13 <= len(digitos) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digitos)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class Hallazgo:
    conversacion_id: str
    turno: int | None  # None = campo de la conversación, no del texto
    tipo: str
    detector: str

    def a_dict(self) -> dict:
        return asdict(self)


def revisar_texto(texto: str) -> list[tuple[str, str]]:
    """Devuelve (tipo, detector) por cada posible dato personal residual."""
    limpio, _ = proteger_radicados(texto)
    hallazgos: list[tuple[str, str]] = []
    for ent in detect(limpio):
        hallazgos.append((ent.type.value, "datashield"))
    if _CORREO.search(limpio):
        hallazgos.append(("CORREO", "regex"))
    if _CELULAR.search(limpio):
        hallazgos.append(("TELEFONO", "regex"))
    if any(_luhn(m.group(0)) for m in _TARJETA.finditer(limpio)):
        hallazgos.append(("TARJETA_CREDITO", "regex"))
    if _CEDULA_PUNTOS.search(limpio):
        hallazgos.append(("CEDULA", "regex"))
    if _NOMBRE_CONTEXTO.search(limpio):
        hallazgos.append(("NOMBRE", "regex"))
    if _DIGITOS_LARGOS.search(limpio):
        hallazgos.append(("NUMERO_LARGO", "regex"))
    return hallazgos


def revisar_conversacion(conv: Conversacion) -> list[Hallazgo]:
    hallazgos = [
        Hallazgo(conv.id, i, tipo, detector)
        for i, turno in enumerate(conv.turnos)
        for tipo, detector in revisar_texto(turno.texto)
    ]
    if conv.cliente_id and not conv.cliente_id.startswith(PREFIJO_HASH):
        hallazgos.append(Hallazgo(conv.id, None, "CLIENTE_ID_SIN_SEUDONIMO", "estructura"))
    return hallazgos


@dataclass
class ResultadoCompuerta:
    aprobadas: list[Conversacion]
    en_cuarentena: list[str]
    hallazgos: list[Hallazgo]

    @property
    def limpio(self) -> bool:
        return not self.hallazgos

    def resumen(self) -> dict:
        por_tipo: dict[str, int] = {}
        for h in self.hallazgos:
            por_tipo[h.tipo] = por_tipo.get(h.tipo, 0) + 1
        return {
            "aprobadas": len(self.aprobadas),
            "en_cuarentena": len(self.en_cuarentena),
            "hallazgos_por_tipo": dict(sorted(por_tipo.items())),
        }


def compuerta(convs: list[Conversacion]) -> ResultadoCompuerta:
    """Separa las conversaciones limpias de las que aún tienen algo sospechoso.

    Las sospechosas quedan en cuarentena local para revisión humana: no se exportan.
    """
    aprobadas: list[Conversacion] = []
    cuarentena: list[str] = []
    hallazgos: list[Hallazgo] = []
    for conv in convs:
        h = revisar_conversacion(conv)
        if h:
            cuarentena.append(conv.id)
            hallazgos.extend(h)
        else:
            aprobadas.append(conv)
    return ResultadoCompuerta(aprobadas, cuarentena, hallazgos)
