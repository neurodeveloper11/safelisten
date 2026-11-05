"""La garantía central: ningún dato personal sembrado en los datos sale de la zona local."""

import re

from alometro.cargar import a_jsonl
from alometro.modelos import Conversacion, Turno
from alometro.sintetico import AGENTES, CLIENTES
from conftest import SAL

from safelisten.edge import (
    anonimizar_lote,
    anonimizar_texto,
    documentos_por_contexto,
    seudonimo_cliente,
)
from safelisten.gate import compuerta, revisar_texto

_DOC = re.compile(r"(?<![\d$])\d{1,3}(?:\.\d{3}){2,3}(?!\d)")


def _conv(*turnos, cliente="cli-000123", **kw):
    return Conversacion(id="t-1", turnos=[Turno(r, t) for r, t in turnos], cliente_id=cliente, **kw)


def test_nada_sembrado_sobrevive(convs_antes, convs_despues):
    """Cédulas, nombres de clientes e ids originales no aparecen en lo exportado."""
    originales = convs_antes + convs_despues
    anonimas, _ = anonimizar_lote(originales, SAL)
    exportado = a_jsonl(compuerta(anonimas).aprobadas)

    cedulas = {m.group(0) for c in originales for t in c.turnos if t.rol == "cliente" for m in _DOC.finditer(t.texto)}
    assert cedulas, "los datos de prueba deberían traer cédulas"
    for cedula in cedulas:
        assert cedula not in exportado
    for c in originales:
        assert f'"{c.cliente_id}"' not in exportado
    nombres_clientes = {n for n in CLIENTES if n not in AGENTES}
    for nombre in nombres_clientes:
        assert re.search(rf"\b{re.escape(nombre)}\b", exportado) is None, nombre


def test_compuerta_limpia_en_datos_sinteticos(convs_antes, convs_despues):
    anonimas, stats = anonimizar_lote(convs_antes + convs_despues, SAL)
    r = compuerta(anonimas)
    assert r.limpio, r.resumen()
    assert stats.reemplazos_por_tipo["CEDULA"] > 0
    assert stats.reemplazos_por_tipo["NOMBRE"] > 0


def test_cedula_dictada_varios_turnos_despues():
    conv = _conv(
        ("agente", "Por favor me regala su número de cédula."),
        ("cliente", "Ya se la di al bot hace un minuto, otra vez??"),
        ("agente", "Es necesario para validar la información."),
        ("cliente", "12.635.880"),
    )
    anon, stats = anonimizar_lote([conv], SAL)
    assert anon[0].turnos[3].texto == "[CEDULA]"
    assert stats.reemplazos_por_tipo["CEDULA"] == 1


def test_montos_no_son_cedulas():
    texto, n = documentos_por_contexto("Su deuda es de $1.200.000 y la cuota de $ 480.000", "¿Me da su cédula?")
    assert n == 0 and "$1.200.000" in texto
    assert revisar_texto("Le llamo por su deuda de $1.200.000.") == []


def test_radicado_se_conserva():
    salida = anonimizar_texto("Su número de radicado es 2026-000123456 y su celular 3001234567")
    assert "2026-000123456" in salida
    assert "3001234567" not in salida


def test_correo_telefono_tarjeta():
    salida = anonimizar_texto("Escríbame a ana.perez@correo.com o al 310 555 1234, tarjeta 4111 1111 1111 1111")
    for dato in ("ana.perez@correo.com", "310 555 1234", "4111 1111 1111 1111"):
        assert dato not in salida


def test_seudonimo_estable_y_dependiente_de_la_sal():
    a = seudonimo_cliente("cli-1", SAL)
    assert a == seudonimo_cliente("cli-1", SAL)  # permite medir recontacto
    assert a != seudonimo_cliente("cli-1", b"otra-sal-de-16-bytes")
    assert a.startswith("h:") and "cli-1" not in a
    assert seudonimo_cliente(None, SAL) is None


def test_sal_corta_rechazada():
    import pytest

    with pytest.raises(ValueError):
        anonimizar_lote([], b"corta")


def test_metadatos_fuera_de_lista_blanca_se_descartan():
    conv = _conv(("cliente", "hola"), metadatos={"escenario": "x", "telefono_cliente": "3001234567"})
    anon, stats = anonimizar_lote([conv], SAL)
    assert anon[0].metadatos == {"escenario": "x"}
    assert stats.metadatos_descartados == 1


def test_compuerta_pone_en_cuarentena_lo_que_se_escapa():
    escapada = _conv(("cliente", "mi otro número es 3159876543"), cliente="h:abc")
    sin_seudonimo = _conv(("cliente", "hola"), cliente="cli-999")
    r = compuerta([escapada, sin_seudonimo])
    assert r.aprobadas == []
    assert len(r.en_cuarentena) == 2
    tipos = {h.tipo for h in r.hallazgos}
    assert {"TELEFONO", "CLIENTE_ID_SIN_SEUDONIMO"} <= tipos
    # Los hallazgos no guardan el valor encontrado.
    assert "3159876543" not in str([h.a_dict() for h in r.hallazgos])


def test_nombres_de_pila_sueltos():
    assert (
        anonimizar_texto("Entiendo su preocupación, Jorge, ya lo reviso")
        == "Entiendo su preocupación, [NOMBRE], ya lo reviso"
    )
    assert anonimizar_texto("¿Hablo con Luz Dary?") == "¿Hablo con [NOMBRE]?"
    assert anonimizar_texto("Señora Gertrudis, buenos días") == "Señora [NOMBRE], buenos días"


def test_palabras_comunes_que_son_nombres_no_se_tocan_sin_contexto():
    assert anonimizar_texto("La luz se fue ayer") == "La luz se fue ayer"
    assert anonimizar_texto("Gracias por llamar a Banco Cordillera") == "Gracias por llamar a Banco Cordillera"


def test_entidad_cortada_a_mitad_de_palabra():
    """DataShield toma «Sebastián de Conecta»; no debe quedar «[NOMBRE]Ya»."""
    assert (
        anonimizar_texto("Buenos días, le habla Sebastián de ConectaYa.")
        == "Buenos días, le habla [NOMBRE] de ConectaYa."
    )


def test_compuerta_detecta_nombre_por_contexto():
    assert ("NOMBRE", "regex") in revisar_texto("¿Hablo con Gertrudis?")
    assert revisar_texto("señora, tengo un problema") == []


def test_compuerta_tarjeta_valida_por_luhn():
    assert ("TARJETA_CREDITO", "regex") in revisar_texto("número 4111-1111-1111-1111 gracias")
    assert all(t != "TARJETA_CREDITO" for t, _ in revisar_texto("referencia 1234-5678-9012-3456"))
