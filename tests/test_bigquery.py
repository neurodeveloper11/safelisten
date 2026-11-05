"""BigQueryWarehouse con un cliente simulado: usa los tipos reales de google-cloud-bigquery,
pero ninguna llamada sale a Google Cloud."""

import pytest

bigquery = pytest.importorskip("google.cloud.bigquery")

from safelisten.score import ESQUEMAS  # noqa: E402
from safelisten.warehouse import BigQueryWarehouse  # noqa: E402


class _Trabajo:
    def __init__(self, error=None):
        self.error = error

    def result(self):
        if self.error:
            raise self.error


class ClienteFalso:
    def __init__(self, error_delete=None):
        self.consultas, self.cargas, self.error_delete = [], [], error_delete

    def query(self, sql, job_config=None):
        self.consultas.append((sql, job_config))
        return _Trabajo(self.error_delete)

    def load_table_from_json(self, filas, destino, job_config=None):
        self.cargas.append((filas, destino, job_config))
        return _Trabajo()


def test_carga_idempotente_con_esquema():
    cliente = ClienteFalso()
    wh = BigQueryWarehouse("proyecto", "safelisten", cliente=cliente)
    n = wh.cargar("raw_lotes", "lote-1", [{"lote_id": "lote-1", "conversaciones": 3}])
    assert n == 1
    sql, config = cliente.consultas[0]
    assert sql.startswith("delete from `proyecto.safelisten.raw_lotes`")
    assert config.query_parameters[0].value == "lote-1"  # parametrizado, no concatenado
    filas, destino, config = cliente.cargas[0]
    assert destino == "proyecto.safelisten.raw_lotes"
    assert [c.name for c in config.schema] == [c for c, _ in ESQUEMAS["raw_lotes"]]
    assert config.write_disposition == bigquery.WriteDisposition.WRITE_APPEND


def test_primera_carga_sin_tabla():
    cliente = ClienteFalso(error_delete=Exception("404 Not found: Table proyecto:safelisten.raw_lotes"))
    wh = BigQueryWarehouse("proyecto", "safelisten", cliente=cliente)
    assert wh.cargar("raw_lotes", "lote-1", [{"lote_id": "lote-1"}]) == 1


def test_otros_errores_se_propagan():
    wh = BigQueryWarehouse("proyecto", "safelisten", cliente=ClienteFalso(error_delete=RuntimeError("permiso")))
    with pytest.raises(RuntimeError):
        wh.cargar("raw_lotes", "lote-1", [])
