"""Lector del archivo que genera WhatsApp con «Exportar chat» (Android y iPhone).

Formatos que reconoce (una línea por mensaje; las líneas sin encabezado continúan el
mensaje anterior):

    5/11/26, 10:05 - Banco Cordillera: Hola, ¿en qué te ayudo?            (Android)
    [5/11/26, 10:05:12] +57 310 555 1234: Quiero hablar con un asesor      (iPhone)
    05/11/2026 10:05 p. m. - Ana: ...                                       (12 horas)

Privacidad: el encabezado de cada línea trae el nombre o el celular de quien escribe.
Ese dato NO entra en la conversación: los participantes se convierten en etiquetas
anónimas (``participante_1``...) y Alómetro decide quién es el asesor por lo que dice.
El identificador del cliente (su nombre o celular) solo se usa como ``cliente_id``, que
la zona local reemplaza por un seudónimo HMAC antes de exportar.
"""

from __future__ import annotations

import re
import zipfile
from datetime import datetime
from pathlib import Path

from alometro.calendario import HORA_COLOMBIA
from alometro.cargar import desde_dict
from alometro.modelos import Conversacion

_INVISIBLES = re.compile("[‎‏‪-‮﻿]")
_HORA = r"(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?(?:\s*(?P<ampm>[ap])\.?\s*m\.?)?"
_FECHA = r"(?P<d1>\d{1,2})/(?P<d2>\d{1,2})/(?P<a>\d{2,4})"
_RESTO = r"(?P<autor>[^:]{1,80}?):\s?(?P<texto>.*)$"
_IPHONE = re.compile(rf"^\[{_FECHA},?\s+{_HORA}\]\s*{_RESTO}", re.IGNORECASE)
_ANDROID = re.compile(rf"^{_FECHA},?\s+{_HORA}\s+-\s+{_RESTO}", re.IGNORECASE)


def _encabezado(linea: str) -> re.Match | None:
    return _IPHONE.match(linea) or _ANDROID.match(linea)


_SISTEMA = re.compile(r"^(?:\[[^\]]+\]\s*|[\d/]+,?\s+[\d:]+[^-]*-\s+)[^:]*$")  # avisos sin «autor:»
_OMITIDOS = re.compile(
    r"^<?(?:multimedia omitid[oa]|media omitted|imagen omitida|audio omitido|sticker omitido"
    r"|video omitido|documento omitido|se eliminó este mensaje|this message was deleted)>?$",
    re.IGNORECASE,
)


def es_exportacion(texto: str) -> bool:
    """¿El texto parece un chat exportado de WhatsApp? (al menos 2 líneas con encabezado)."""
    lineas = [ln for ln in _INVISIBLES.sub("", texto).splitlines()[:50] if ln.strip()]
    return sum(1 for ln in lineas if _encabezado(ln)) >= 2


def _fecha(m: re.Match, dia_primero: bool) -> datetime:
    d1, d2, anio = int(m["d1"]), int(m["d2"]), int(m["a"])
    dia, mes = (d1, d2) if dia_primero else (d2, d1)
    if anio < 100:
        anio += 2000
    hora = int(m["h"])
    if m["ampm"]:
        hora = hora % 12 + (12 if m["ampm"].lower() == "p" else 0)
    return datetime(anio, mes, dia, hora, int(m["mi"]), int(m["s"] or 0), tzinfo=HORA_COLOMBIA)


def leer(texto: str, id_: str, empresa: list[str] | None = None, dia_primero: bool = True) -> Conversacion:
    """Convierte un chat exportado en una conversación de Alómetro.

    Args:
        empresa: nombres con que aparece la empresa en el chat (p. ej. «Banco Cordillera»).
            Esos participantes se marcan como ``agente``. Si no se indican, Alómetro infiere
            quién atiende por el contenido de los mensajes.
        dia_primero: formato de fecha día/mes (Colombia y casi toda Latinoamérica).
    """
    empresa_norm = {e.strip().casefold() for e in (empresa or [])}
    etiquetas: dict[str, str] = {}
    turnos: list[dict] = []
    inicio: datetime | None = None
    for linea in _INVISIBLES.sub("", texto).splitlines():
        if not linea.strip():
            continue
        m = _encabezado(linea)
        if not m:
            if _SISTEMA.match(linea):
                continue  # «Los mensajes están cifrados…», «X creó el grupo»
            if turnos:
                turnos[-1]["texto"] += "\n" + linea.strip()
            continue
        autor, mensaje = m["autor"].strip(), m["texto"].strip()
        cuando = _fecha(m, dia_primero)
        inicio = inicio or cuando
        if not mensaje or _OMITIDOS.match(mensaje):
            continue
        etiqueta = etiquetas.setdefault(autor, f"participante_{len(etiquetas) + 1}")
        t = (cuando - inicio).total_seconds()
        if turnos and turnos[-1]["rol"] == etiqueta:
            turnos[-1]["texto"] += "\n" + mensaje  # mensajes seguidos de la misma persona
        else:
            turnos.append({"rol": etiqueta, "texto": mensaje, "t": t})
    if not turnos:
        raise ValueError(f"{id_}: no parece un chat exportado de WhatsApp (no se encontraron mensajes).")
    roles = {etq: "agente" for autor, etq in etiquetas.items() if autor.casefold() in empresa_norm}
    conv = desde_dict(
        {"id": id_, "canal": "whatsapp", "inicio": inicio.isoformat() if inicio else None, "turnos": turnos},
        roles or None,
    )
    asignados = {k.lower(): v for k, v in {**conv.metadatos.get("roles_inferidos", {}), **roles}.items()}
    clientes = [autor for autor, etq in etiquetas.items() if asignados.get(etq) == "cliente"]
    conv.cliente_id = clientes[0] if clientes else None  # la zona local lo seudonimiza
    conv.metadatos = {
        "origen": "whatsapp",
        "roles_inferidos": {k: v for k, v in asignados.items() if not k.startswith("_")},
    }
    return conv


def texto_de_archivo(ruta: str | Path) -> str:
    """Texto del chat. Acepta el .txt o el .zip que genera WhatsApp (solo se lee el .txt; las
    fotos, audios y documentos adjuntos no se abren)."""
    ruta = Path(ruta)
    if ruta.suffix.lower() == ".zip":
        with zipfile.ZipFile(ruta) as z:
            nombres = [n for n in z.namelist() if n.lower().endswith(".txt") and not n.startswith("__MACOSX")]
            if not nombres:
                raise ValueError(f"{ruta.name}: el .zip no contiene el chat (.txt).")
            principal = next((n for n in nombres if Path(n).name.lower() == "_chat.txt"), nombres[0])
            return z.read(principal).decode("utf-8-sig", errors="replace")
    return ruta.read_text(encoding="utf-8-sig", errors="replace")


def cargar_archivo(ruta: str | Path, empresa: list[str] | None = None) -> Conversacion:
    ruta = Path(ruta)
    return leer(texto_de_archivo(ruta), ruta.stem, empresa)
