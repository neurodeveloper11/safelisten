import json

import pytest
from alometro import calcular_indicadores, evaluar
from conftest import SAL

from safelisten import manifest as mf
from safelisten.edge import anonimizar_lote
from safelisten.pipeline import LoteRechazado, exportar_conversaciones, procesar, procesar_directorio
from safelisten.warehouse import DuckDBWarehouse


@pytest.fixture
def lote(tmp_path, convs_antes):
    return exportar_conversaciones(convs_antes, tmp_path / "lote", SAL, "Prueba", lote_id="lote-prueba")


def test_exportar_escribe_lote_y_manifiesto(lote):
    manifiesto, datos = mf.leer(lote.directorio)
    assert manifiesto["esquema"] == mf.ESQUEMA
    assert manifiesto["archivo"]["sha256"] == mf.sha256_bytes(datos)
    assert manifiesto["compuerta"]["en_cuarentena"] == 0
    assert manifiesto["privacidad"]["reemplazos_total"] > 0
    assert set(manifiesto["software"]) == {"safelisten", "datashield-es", "alometro"}


def test_procesar_carga_el_warehouse(tmp_path, lote, convs_antes):
    wh = DuckDBWarehouse(tmp_path / "wh.duckdb")
    r = procesar_directorio(lote.directorio, wh)
    assert r.conversaciones == len(convs_antes)
    n = wh.consultar("select count(*) as n from raw_evaluaciones")[0]["n"]
    assert n == len(convs_antes)
    assert wh.consultar("select count(*) as n from raw_criterios")[0]["n"] > n
    assert wh.consultar("select reemplazos_pii from raw_lotes")[0]["reemplazos_pii"] > 0
    # Ningún cliente_hash queda en claro.
    assert wh.consultar("select count(*) as n from raw_evaluaciones where cliente_hash not like 'h:%'")[0]["n"] == 0


def test_reprocesar_no_duplica(tmp_path, lote, convs_antes):
    wh = DuckDBWarehouse(tmp_path / "wh.duckdb")
    procesar_directorio(lote.directorio, wh)
    procesar_directorio(lote.directorio, wh)
    assert wh.consultar("select count(*) as n from raw_evaluaciones")[0]["n"] == len(convs_antes)


def test_archivo_alterado_se_rechaza(tmp_path, lote):
    manifiesto, datos = mf.leer(lote.directorio)
    with pytest.raises(LoteRechazado, match="SHA-256"):
        procesar(manifiesto, datos + b"\n", DuckDBWarehouse(tmp_path / "wh.duckdb"))


def test_nube_rechaza_lote_con_datos_personales(tmp_path):
    """Aunque alguien fabrique un manifiesto válido, la compuerta de la nube detiene el lote."""
    linea = {"id": "x-1", "cliente_id": "h:abc", "turnos": [{"rol": "cliente", "texto": "mi correo es ana@correo.com"}]}
    datos = (json.dumps(linea) + "\n").encode()
    manifiesto = mf.construir(datos, {}, {"aprobadas": 1, "en_cuarentena": 0}, "Atacante")
    wh = DuckDBWarehouse(tmp_path / "wh.duckdb")
    with pytest.raises(LoteRechazado, match="compuerta"):
        procesar(manifiesto, datos, wh)
    assert wh.consultar("select count(*) as n from raw_evaluaciones")[0]["n"] == 0


def test_anonimizar_no_altera_la_medicion(convs_antes, convs_despues):
    """Verificación clave: los indicadores son los mismos con y sin datos personales.

    Única diferencia conocida: en el texto original, que el cliente dicte su cédula dos
    veces cuenta también como «repitió su mensaje» (criterio E5), además de «dato
    repetido». Con «[CEDULA]» ese doble conteo desaparece. Por eso solo cambian E5 y el
    puntaje promedio (I16), siempre en esa dirección y en pocos puntos.
    """
    originales = convs_antes + convs_despues
    anonimas, _ = anonimizar_lote(originales, SAL)
    ev_orig = [evaluar(c) for c in originales]
    ev_anon = [evaluar(c) for c in anonimas]
    for a, b in zip(ev_orig, ev_anon):
        distintos = {x.id for x, y in zip(a.criterios, b.criterios) if x.cumple != y.cumple}
        assert distintos <= {"E5"}, (a.conversacion_id, distintos)
        if distintos:
            assert b.puntaje >= a.puntaje
    ind_a = {i.id: i.valor for i in calcular_indicadores(ev_orig)["indicadores"]}
    ind_b = {i.id: i.valor for i in calcular_indicadores(ev_anon)["indicadores"]}
    assert {k for k in ind_a if ind_a[k] != ind_b[k]} <= {"I16"}
    assert abs(ind_a["I16"] - ind_b["I16"]) < 1.0
