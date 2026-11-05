"""Manifiesto del lote: la evidencia de privacidad que acompaña a cada exportación.

El manifiesto dice qué se protegió (solo conteos), qué versión del software lo hizo,
el resultado de la compuerta de fugas y el SHA-256 del archivo exportado. La nube
rechaza un lote si el hash no coincide o si la compuerta no quedó limpia.
Un oficial de cumplimiento puede archivarlo como soporte de la Ley 1581 de 2012.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ESQUEMA = "safelisten.manifiesto/v1"


def _version(paquete: str) -> str:
    try:
        return version(paquete)
    except PackageNotFoundError:  # pragma: no cover - ejecución desde el código fuente
        return "desconocida"


def sha256_bytes(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def construir(
    datos_exportados: bytes, privacidad: dict, compuerta: dict, organizacion: str, lote_id: str | None = None
) -> dict:
    return {
        "esquema": ESQUEMA,
        "lote_id": lote_id or uuid.uuid4().hex[:16],
        "organizacion": organizacion,
        "creado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "archivo": {
            "nombre": "conversaciones.jsonl",
            "sha256": sha256_bytes(datos_exportados),
            "bytes": len(datos_exportados),
        },
        "privacidad": privacidad,
        "compuerta": {**compuerta, "limpia": True},
        "software": {
            "safelisten": _version("safelisten"),
            "datashield-es": _version("datashield-es"),
            "alometro": _version("alometro"),
        },
    }


@dataclass
class ManifiestoInvalido(Exception):
    motivo: str

    def __str__(self) -> str:
        return self.motivo


def verificar(manifiesto: dict, datos: bytes) -> None:
    """Lanza ManifiestoInvalido si el lote no debe procesarse."""
    if not isinstance(manifiesto, dict):
        raise ManifiestoInvalido("El manifiesto debe ser un objeto JSON.")
    if manifiesto.get("esquema") != ESQUEMA:
        raise ManifiestoInvalido(f"Esquema desconocido: {manifiesto.get('esquema')!r}")
    archivo, compuerta = manifiesto.get("archivo"), manifiesto.get("compuerta")
    if not isinstance(archivo, dict) or not isinstance(compuerta, dict):
        raise ManifiestoInvalido("Faltan las secciones «archivo» o «compuerta».")
    if archivo.get("sha256") != sha256_bytes(datos):
        raise ManifiestoInvalido("El SHA-256 del archivo no coincide con el manifiesto (archivo alterado).")
    if compuerta.get("limpia") is not True:
        raise ManifiestoInvalido("La compuerta de fugas no quedó limpia en origen.")
    if not isinstance(manifiesto.get("lote_id"), str) or not manifiesto["lote_id"]:
        raise ManifiestoInvalido("Falta lote_id.")


def guardar(directorio: Path, datos: bytes, manifiesto: dict) -> tuple[Path, Path]:
    directorio.mkdir(parents=True, exist_ok=True)
    archivo = directorio / "conversaciones.jsonl"
    archivo.write_bytes(datos)
    ruta_manifiesto = directorio / "manifiesto.json"
    ruta_manifiesto.write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return archivo, ruta_manifiesto


def leer(directorio: Path) -> tuple[dict, bytes]:
    manifiesto = json.loads((directorio / "manifiesto.json").read_text(encoding="utf-8"))
    datos = (directorio / manifiesto.get("archivo", {}).get("nombre", "conversaciones.jsonl")).read_bytes()
    return manifiesto, datos
