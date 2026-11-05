"""Línea de comandos.

safelisten sal                                   # genera la sal secreta (se guarda en la empresa)
safelisten exportar llamadas.xlsx -o lote/ --org "Mi Empresa"   # zona local
safelisten enviar lote/ --url https://...run.app                # sube el lote a la nube
safelisten procesar lote/ --duckdb safelisten.duckdb            # nube (o local para pruebas)
safelisten demo -o demo/                         # todo el flujo con datos sintéticos
safelisten servir                                # API (Cloud Run usa este comando)
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.request
import uuid
from pathlib import Path

from . import __version__
from . import manifest as mf
from .pipeline import LoteRechazado, exportar, exportar_conversaciones, procesar_directorio
from .warehouse import DuckDBWarehouse


def _sal(args: argparse.Namespace) -> bytes:
    if args.sal_archivo:
        texto = Path(args.sal_archivo).read_text(encoding="utf-8").strip()
    else:
        texto = os.environ.get("SAFELISTEN_SAL", "")
    if not texto:
        sys.exit("Falta la sal: use --sal-archivo o la variable SAFELISTEN_SAL (genérela con `safelisten sal`).")
    return bytes.fromhex(texto)


def enviar_lote(directorio: Path, url: str, token: str) -> dict:
    manifiesto, datos = mf.leer(directorio)
    limite = uuid.uuid4().hex
    partes = []
    for campo, nombre, contenido, tipo in (
        ("manifiesto", "manifiesto.json", json.dumps(manifiesto).encode(), "application/json"),
        ("conversaciones", "conversaciones.jsonl", datos, "application/x-ndjson"),
    ):
        partes.append(
            f'--{limite}\r\nContent-Disposition: form-data; name="{campo}"; filename="{nombre}"\r\n'
            f"Content-Type: {tipo}\r\n\r\n".encode()
            + contenido
            + b"\r\n"
        )
    cuerpo = b"".join(partes) + f"--{limite}--\r\n".encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/lotes",
        data=cuerpo,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={limite}", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 - URL dada por el operador
        return json.loads(resp.read())


def _imprimir_indicadores(resultado) -> None:
    for ind in resultado.resumen["indicadores"]:
        valor = "—" if ind.valor is None else (f"{ind.valor:.0%}" if ind.unidad == "%" else f"{ind.valor:.1f}")
        print(f"  {ind.id} {ind.nombre:<42} {valor:>7}  ({ind.estado})")


def cmd_exportar(args: argparse.Namespace) -> None:
    r = exportar(args.entradas, args.salida, _sal(args), args.org)
    p = r.manifiesto["privacidad"]
    print(
        f"Lote {r.manifiesto['lote_id']}: {r.manifiesto['compuerta']['aprobadas']} conversaciones "
        f"listas en {r.directorio}"
    )
    print(f"  Datos personales reemplazados: {p['reemplazos_total']} {p['reemplazos_por_tipo']}")
    if r.en_cuarentena:
        print(f"  En cuarentena (no se exportan, revisar a mano): {len(r.en_cuarentena)} -> cuarentena.txt")


def cmd_procesar(args: argparse.Namespace) -> None:
    wh = DuckDBWarehouse(args.duckdb)
    try:
        r = procesar_directorio(args.directorio, wh)
    except LoteRechazado as e:
        sys.exit(f"Lote rechazado: {e}")
    print(f"Lote {r.lote_id}: {r.conversaciones} conversaciones calificadas y cargadas en {args.duckdb}")
    _imprimir_indicadores(r)


def cmd_enviar(args: argparse.Namespace) -> None:
    token = args.token or os.environ.get("SAFELISTEN_TOKEN")
    if not token:
        sys.exit("Falta el token: --token o SAFELISTEN_TOKEN.")
    print(json.dumps(enviar_lote(Path(args.directorio), args.url, token), ensure_ascii=False, indent=2))


def cmd_demo(args: argparse.Namespace) -> None:
    from alometro import comparar
    from alometro.informe import generar_html
    from alometro.sintetico import generar

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    sal = secrets.token_bytes(32)
    wh = DuckDBWarehouse(salida / "safelisten.duckdb")
    resultados = {}
    for periodo in ("antes", "despues"):
        convs = generar(args.n, periodo)
        exp = exportar_conversaciones(
            convs, salida / f"lote_{periodo}", sal, "Operación de demostración", lote_id=f"demo-{periodo}"
        )
        r = procesar_directorio(exp.directorio, wh)
        resultados[periodo] = (exp, r)
        print(
            f"[{periodo}] {r.conversaciones} conversaciones · "
            f"{exp.manifiesto['privacidad']['reemplazos_total']} datos personales reemplazados · "
            f"cuarentena: {len(exp.en_cuarentena)}"
        )
    antes, despues = resultados["antes"][1], resultados["despues"][1]
    informe = generar_html(
        despues.evaluaciones,
        despues.resumen,
        titulo="SafeListen · Informe de ejemplo",
        fuente="Datos sintéticos anonimizados con DataShield",
    )
    (salida / "informe.html").write_text(informe, encoding="utf-8")
    cambios = comparar(antes.evaluaciones, despues.evaluaciones)
    resumen = {
        "antes": antes.a_dict(),
        "despues": despues.a_dict(),
        "privacidad": {p: resultados[p][0].manifiesto["privacidad"] for p in resultados},
        "comparacion": cambios,
    }
    (salida / "resumen.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print("Indicadores del periodo 'después':")
    _imprimir_indicadores(despues)
    print(f"Informe: {salida / 'informe.html'} · Warehouse: {salida / 'safelisten.duckdb'}")


def cmd_servir(args: argparse.Namespace) -> None:  # pragma: no cover - arranca un servidor
    import uvicorn

    from .api import crear_app

    uvicorn.run(crear_app(), host="0.0.0.0", port=int(os.environ.get("PORT", args.puerto)))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="safelisten", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=f"safelisten {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sal", help="Genera una sal secreta de 32 bytes (hex).").set_defaults(
        fn=lambda a: print(secrets.token_hex(32))
    )

    e = sub.add_parser("exportar", help="Zona local: anonimiza y prepara el lote.")
    e.add_argument("entradas", nargs="+", help="Archivos o carpetas (.jsonl, .csv, .xlsx, .txt, .srt, .vtt).")
    e.add_argument("-o", "--salida", required=True)
    e.add_argument("--org", required=True, help="Nombre de la organización (va en el manifiesto).")
    e.add_argument("--sal-archivo", help="Archivo con la sal en hex (si no, SAFELISTEN_SAL).")
    e.set_defaults(fn=cmd_exportar)

    pr = sub.add_parser("procesar", help="Verifica, califica y carga un lote en DuckDB.")
    pr.add_argument("directorio")
    pr.add_argument("--duckdb", default="safelisten.duckdb")
    pr.set_defaults(fn=cmd_procesar)

    en = sub.add_parser("enviar", help="Sube un lote a la API de SafeListen.")
    en.add_argument("directorio")
    en.add_argument("--url", required=True)
    en.add_argument("--token")
    en.set_defaults(fn=cmd_enviar)

    d = sub.add_parser("demo", help="Flujo completo con datos sintéticos (antes/después).")
    d.add_argument("-o", "--salida", default="demo")
    d.add_argument("-n", type=int, default=400)
    d.set_defaults(fn=cmd_demo)

    s = sub.add_parser("servir", help="Arranca la API.")
    s.add_argument("--puerto", type=int, default=8080)
    s.set_defaults(fn=cmd_servir)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    main()
