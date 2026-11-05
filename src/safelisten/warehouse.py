"""Destino de las filas: DuckDB (local, pruebas y demo) o BigQuery (producción en GCP).

Las cargas son idempotentes por lote: reenviar el mismo ``lote_id`` reemplaza sus
filas en vez de duplicarlas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .score import ESQUEMAS

_DUCKDB_TIPOS = {"STRING": "VARCHAR", "INT64": "BIGINT", "FLOAT64": "DOUBLE", "BOOL": "BOOLEAN"}


class Warehouse(Protocol):
    def cargar(self, tabla: str, lote_id: str, filas: list[dict]) -> int: ...


class DuckDBWarehouse:
    def __init__(self, ruta: str | Path = "safelisten.duckdb", esquema: str = "main") -> None:
        import duckdb

        self.ruta = str(ruta)
        self.esquema = esquema
        self.con = duckdb.connect(self.ruta)
        self.con.execute(f"create schema if not exists {esquema}")
        for tabla, columnas in ESQUEMAS.items():
            cols = ", ".join(f"{c} {_DUCKDB_TIPOS[t]}" for c, t in columnas)
            self.con.execute(f"create table if not exists {esquema}.{tabla} ({cols})")

    def cargar(self, tabla: str, lote_id: str, filas: list[dict]) -> int:
        columnas = [c for c, _ in ESQUEMAS[tabla]]
        self.con.execute("begin")
        try:
            self.con.execute(f"delete from {self.esquema}.{tabla} where lote_id = ?", [lote_id])
            if filas:
                marcas = ", ".join("?" for _ in columnas)
                self.con.executemany(
                    f"insert into {self.esquema}.{tabla} ({', '.join(columnas)}) values ({marcas})",
                    [[f.get(c) for c in columnas] for f in filas],
                )
            self.con.execute("commit")
        except Exception:
            self.con.execute("rollback")
            raise
        return len(filas)

    def consultar(self, sql: str, parametros: list | None = None) -> list[dict]:
        cur = self.con.execute(sql, parametros or [])
        nombres = [d[0] for d in cur.description]
        return [dict(zip(nombres, fila)) for fila in cur.fetchall()]

    def cerrar(self) -> None:
        self.con.close()


class BigQueryWarehouse:
    """Carga en BigQuery con ``load_table_from_json``. Requiere el extra ``[gcp]``.

    ``cliente`` se puede inyectar (pruebas); si no, se usa el de Application Default
    Credentials (en Cloud Run, la cuenta de servicio del servicio).
    """

    def __init__(self, proyecto: str, dataset: str, cliente=None) -> None:
        if cliente is None:  # pragma: no cover - requiere credenciales de GCP
            from google.cloud import bigquery

            cliente = bigquery.Client(project=proyecto)
        self.cliente = cliente
        self.proyecto = proyecto
        self.dataset = dataset

    def _schema(self, tabla: str):
        from google.cloud import bigquery

        return [bigquery.SchemaField(c, t, mode="NULLABLE") for c, t in ESQUEMAS[tabla]]

    def cargar(self, tabla: str, lote_id: str, filas: list[dict]) -> int:
        from google.cloud import bigquery

        destino = f"{self.proyecto}.{self.dataset}.{tabla}"
        try:
            self.cliente.query(
                f"delete from `{destino}` where lote_id = @lote",
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("lote", "STRING", lote_id)]
                ),
            ).result()
        except Exception as e:  # la tabla aún no existe en la primera carga
            if "Not found" not in str(e):
                raise
        if filas:
            config = bigquery.LoadJobConfig(
                schema=self._schema(tabla),
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
            )
            self.cliente.load_table_from_json(filas, destino, job_config=config).result()
        return len(filas)
