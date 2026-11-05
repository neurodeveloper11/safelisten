from conftest import SAL

from safelisten.cli import main
from safelisten.warehouse import DuckDBWarehouse


def test_exportar_y_procesar_por_cli(tmp_path, capsys, monkeypatch):
    entrada = tmp_path / "llamada.txt"
    entrada.write_text(
        "Agente: Buenos días, le habla Laura. ¿Me confirma su número de cédula?\n"
        "Cliente: 1.144.123.456. Quiero cancelar el plan\n"
        "Agente: Listo, quedó cancelado. Su radicado es 2026-777888.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SAFELISTEN_SAL", SAL.hex())
    main(["exportar", str(entrada), "-o", str(tmp_path / "lote"), "--org", "Prueba"])
    assert "1 conversaciones listas" in capsys.readouterr().out
    main(["procesar", str(tmp_path / "lote"), "--duckdb", str(tmp_path / "wh.duckdb")])
    salida = capsys.readouterr().out
    assert "calificadas y cargadas" in salida and "I16" in salida
    wh = DuckDBWarehouse(tmp_path / "wh.duckdb")
    assert wh.consultar("select count(*) as n from raw_evaluaciones")[0]["n"] == 1


def test_sal_genera_64_hex(capsys):
    main(["sal"])
    assert len(capsys.readouterr().out.strip()) == 64
