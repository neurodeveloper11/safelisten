"""Zona local (on-premise): anonimiza las conversaciones ANTES de que salgan de la empresa.

Este módulo corre en un servidor o portátil de la empresa cliente. Nada de lo que
hace requiere red. Lo que produce (texto anonimizado + manifiesto) es lo único que
se envía a la nube.

Qué hace con cada conversación:

* Texto de cada turno: DataShield reemplaza cédulas, NIT, teléfonos, correos,
  direcciones, tarjetas, cuentas, placas, fechas de nacimiento y nombres por
  etiquetas (``[CEDULA]``, ``[NOMBRE]``...). Los radicados (CUN, número de caso)
  se conservan porque son evidencia de cumplimiento y no identifican a la persona.
* ``cliente_id``: se reemplaza por un HMAC-SHA256 con una sal secreta que nunca
  sale de la empresa. Así se puede medir el recontacto a 7 días (mismo cliente)
  sin conocer quién es.
* ``metadatos``: solo pasan las claves de una lista blanca; el resto se descarta.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter
from dataclasses import dataclass, field, replace

from alometro.modelos import Conversacion, Turno
from datashield_es import detect
from datashield_es._names import AMBIGUOUS, FIRST_NAMES, SURNAMES, normalize
from datashield_es.anonymize import replace_entities

# Mismo criterio que Alómetro (privacidad.py): el radicado se conserva.
RADICADO = re.compile(
    r"\b(radicado|cun|caso|ticket|solicitud|seguimiento|pedido|orden)\b(\D{0,25}?)([A-Za-z0-9-]*\d{4,}[A-Za-z0-9-]*)",
    re.IGNORECASE,
)

METADATOS_PERMITIDOS = frozenset({"escenario", "origen", "roles_inferidos", "campana", "cola"})
PREFIJO_HASH = "h:"


@dataclass
class EstadisticasPrivacidad:
    """Cuenta lo que se protegió. Va al manifiesto (sin los valores, solo conteos)."""

    conversaciones: int = 0
    turnos: int = 0
    reemplazos_por_tipo: Counter = field(default_factory=Counter)
    clientes_seudonimizados: int = 0
    metadatos_descartados: int = 0
    reemplazos_por_regla_dialogo: int = 0
    reemplazos_por_regla_nombres: int = 0

    @property
    def reemplazos_total(self) -> int:
        return sum(self.reemplazos_por_tipo.values())

    def a_dict(self) -> dict:
        return {
            "conversaciones": self.conversaciones,
            "turnos": self.turnos,
            "reemplazos_total": self.reemplazos_total,
            "reemplazos_por_tipo": dict(sorted(self.reemplazos_por_tipo.items())),
            "reemplazos_por_regla_dialogo": self.reemplazos_por_regla_dialogo,
            "reemplazos_por_regla_nombres": self.reemplazos_por_regla_nombres,
            "clientes_seudonimizados": self.clientes_seudonimizados,
            "metadatos_descartados": self.metadatos_descartados,
        }


def proteger_radicados(texto: str) -> tuple[str, dict[str, str]]:
    """Cambia los números de radicado por marcadores para que el anonimizador no los toque."""
    marcas: dict[str, str] = {}

    def _sub(m: re.Match) -> str:
        clave = f"RADQZ{len(marcas)}QZ"
        marcas[clave] = m.group(3)
        return f"{m.group(1)}{m.group(2)}{clave}"

    return RADICADO.sub(_sub, texto), marcas


def restaurar_radicados(texto: str, marcas: dict[str, str]) -> str:
    for clave, valor in marcas.items():
        texto = texto.replace(clave, valor)
    return texto


def seudonimo_cliente(cliente_id: str | None, sal: bytes) -> str | None:
    """HMAC-SHA256 truncado. Sin la sal no se puede revertir ni comprobar por fuerza bruta."""
    if not cliente_id:
        return None
    if cliente_id.startswith(PREFIJO_HASH):
        return cliente_id  # ya viene seudonimizado
    digest = hmac.new(sal, cliente_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{PREFIJO_HASH}{digest[:20]}"


# Regla de diálogo. DataShield reconoce una cédula cuando la palabra clave va pegada al
# número («mi cédula es 1.234.567»). En una conversación es común que el agente o el bot
# pida el documento en un turno y el cliente responda solo con el número en el siguiente:
# «¿Me confirma su cédula?» → «234.888.235». Esa respuesta se trata como documento.
PIDE_DOCUMENTO = re.compile(
    r"c[eé]dula|documento|identificaci[oó]n|\bnit\b|\bc\.?\s?c\b|pasaporte|tarjeta\s+de\s+identidad",
    re.IGNORECASE,
)
NUMERO_DOCUMENTO = re.compile(
    r"(?<![\d$.,])(?<!\$ )(\d{1,3}(?:\.\d{3}){1,3}|\d{1,4}(?: \d{3}){2}|\d{6,10})(?![\d]|[.,]\d)"
)


VENTANA_CONTEXTO = 3


def _digitos(s: str) -> int:
    return sum(c.isdigit() for c in s)


def documentos_por_contexto(texto: str, turno_anterior: str | None) -> tuple[str, int]:
    """Reemplaza números de documento dictados como respuesta a una solicitud de documento."""
    if not turno_anterior or not PIDE_DOCUMENTO.search(turno_anterior):
        return texto, 0
    n = 0

    def _sub(m: re.Match) -> str:
        nonlocal n
        if 6 <= _digitos(m.group(1)) <= 10:
            n += 1
            return "[CEDULA]"
        return m.group(0)

    return NUMERO_DOCUMENTO.sub(_sub, texto), n


# Regla de nombres de pila. DataShield marca un nombre cuando trae apellido o una palabra
# de contexto («señor», «me llamo»). En atención al cliente el nombre de pila suelto es lo
# más común: «Entiendo su preocupación, Jorge, …», «¿Hablo con Luz Dary?». Se usa la lista
# de nombres de DataShield; los que también son palabras comunes (Luz, Rosa, Esperanza…)
# solo se reemplazan con contexto o cuando van seguidos de otro nombre propio.
PALABRAS_COMUNES = (
    frozenset(
        {
            "luz",
            "flor",
            "rosa",
            "clara",
            "gloria",
            "esperanza",
            "paz",
            "pilar",
            "consuelo",
            "amparo",
            "mercedes",
            "dolores",
            "alba",
            "blanca",
            "francia",
            "victoria",
            "angel",
            "cruz",
            "jesus",
            "salvador",
            "reina",
            "sol",
            "fe",
            "rocio",
            "aurora",
            "soledad",
            "milagros",
            "socorro",
        }
    )
    | AMBIGUOUS
)
_PALABRA = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+"
NOMBRE_PROPIO = re.compile(rf"\b{_PALABRA}(?:\s+{_PALABRA}){{0,3}}\b")
CONTEXTO_NOMBRE = re.compile(
    r"(?:hablo\s+con|habla\s+con|se[ñn]or(?:a|ita)?|do[ñn]a|don|sr\.?|sra\.?|me\s+llamo|mi\s+nombre\s+es)\s+$",
    re.IGNORECASE,
)


def _es_nombre(token: str) -> bool:
    n = normalize(token)
    return n in FIRST_NAMES or n in SURNAMES


def nombres_de_pila(texto: str) -> tuple[str, int]:
    n = 0

    def _sub(m: re.Match) -> str:
        nonlocal n
        tokens = m.group(0).split()
        primero = normalize(tokens[0])
        con_contexto = bool(CONTEXTO_NOMBRE.search(texto[: m.start()]))
        if primero not in FIRST_NAMES:
            if con_contexto:  # nombre desconocido pero introducido por «hablo con», «señora»…
                n += 1
                return "[NOMBRE]"
            return m.group(0)
        if primero in PALABRAS_COMUNES and not con_contexto and len(tokens) == 1:
            return m.group(0)
        # Se extiende a los nombres o apellidos que siguen («María José Pérez»);
        # tras un nombre ambiguo, a la palabra siguiente («Luz Dary»).
        fin = 1
        while fin < len(tokens) and (_es_nombre(tokens[fin]) or (fin == 1 and primero in PALABRAS_COMUNES)):
            fin += 1
        n += 1
        resto = " ".join(tokens[fin:])
        return "[NOMBRE]" + (" " + resto if resto else "")

    return NOMBRE_PROPIO.sub(_sub, texto), n


_CONECTORES = ("de", "del", "la", "las", "los", "y")


def _recortar(texto: str, ent):
    """Corrige entidades que terminan a mitad de palabra.

    DataShield a veces toma «Sebastián de ConectaYa» como «Sebastián de Conecta» y deja
    «[NOMBRE]Ya». Se recorta la entidad hasta la última palabra completa y se quitan los
    conectores sueltos al final. Si no queda nada, se descarta (la regla de nombres de
    pila se encarga del nombre).
    """
    fin = ent.end
    if not (fin < len(texto) and texto[fin].isalnum() and texto[fin - 1].isalnum()):
        return ent
    palabras = texto[ent.start : fin].split()[:-1]
    while palabras and palabras[-1].lower() in _CONECTORES:
        palabras.pop()
    if not palabras:
        return None
    nuevo = " ".join(palabras)
    return replace(ent, end=ent.start + len(nuevo), text=nuevo)


def entidades(texto: str, contexto: str | None = None) -> list:
    return [e for e in (_recortar(texto, x) for x in detect(texto, contexto=contexto)) if e is not None]


def anonimizar_texto(texto: str, stats: EstadisticasPrivacidad | None = None, turno_anterior: str | None = None) -> str:
    protegido, marcas = proteger_radicados(texto)
    ents = entidades(protegido, turno_anterior)
    salida = replace_entities(protegido, ents, "etiqueta")
    salida, por_contexto = documentos_por_contexto(salida, turno_anterior)
    salida, por_nombre = nombres_de_pila(salida)
    if stats is not None:
        for ent in ents:
            stats.reemplazos_por_tipo[ent.type.value] += 1
        if por_contexto:
            stats.reemplazos_por_tipo["CEDULA"] += por_contexto
            stats.reemplazos_por_regla_dialogo += por_contexto
        if por_nombre:
            stats.reemplazos_por_tipo["NOMBRE"] += por_nombre
            stats.reemplazos_por_regla_nombres += por_nombre
    return restaurar_radicados(salida, marcas)


def anonimizar_conversacion(conv: Conversacion, sal: bytes, stats: EstadisticasPrivacidad) -> Conversacion:
    turnos = []
    for i, t in enumerate(conv.turnos):
        # Contexto: los turnos previos (texto original). La cédula a veces llega varios
        # turnos después de pedirla («¿otra vez?» → «es necesario» → «12.635.880»).
        contexto = "\n".join(p.texto for p in conv.turnos[max(0, i - VENTANA_CONTEXTO) : i]) or None
        turnos.append(Turno(rol=t.rol, texto=anonimizar_texto(t.texto, stats, contexto), t=t.t))
    metadatos = {k: v for k, v in conv.metadatos.items() if k in METADATOS_PERMITIDOS}
    stats.metadatos_descartados += len(conv.metadatos) - len(metadatos)
    stats.conversaciones += 1
    stats.turnos += len(turnos)
    cliente = seudonimo_cliente(conv.cliente_id, sal)
    if cliente and cliente != conv.cliente_id:
        stats.clientes_seudonimizados += 1
    return replace(conv, turnos=turnos, cliente_id=cliente, metadatos=metadatos)


def anonimizar_lote(convs: list[Conversacion], sal: bytes) -> tuple[list[Conversacion], EstadisticasPrivacidad]:
    if len(sal) < 16:
        raise ValueError("La sal debe tener al menos 16 bytes (use `safelisten sal` para generar una).")
    stats = EstadisticasPrivacidad()
    return [anonimizar_conversacion(c, sal, stats) for c in convs], stats
