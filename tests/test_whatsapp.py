import zipfile

import pytest
from conftest import SAL

from safelisten.edge import anonimizar_lote
from safelisten.pipeline import cargar_entradas, exportar
from safelisten.whatsapp import es_exportacion, leer

ANDROID = """5/11/26, 10:04 - Los mensajes y las llamadas están cifrados de extremo a extremo.
5/11/26, 10:05 - Ana María Pérez: Buenas tardes
5/11/26, 10:05 - Ana María Pérez: Me cobraron doble la cuota de noviembre
y no me han devuelto nada
5/11/26, 10:06 - Banco Cordillera: Hola, te habla Laura de Banco Cordillera. ¿Me confirmas tu número de cédula?
5/11/26, 10:07 - Ana María Pérez: 1.144.123.456
5/11/26, 10:07 - Ana María Pérez: <Multimedia omitido>
5/11/26, 10:09 - Banco Cordillera: Ya radiqué la reclamación, tu radicado es 2026-445566. Respuesta en 15 días hábiles.
5/11/26, 10:10 - Ana María Pérez: No, gracias
"""

IPHONE = """‎[5/11/26, 9:15:02 p. m.] Banco Cordillera: Hola, ¿en qué te ayudo?
[5/11/26, 9:16:40 p. m.] +57 310 555 1234: Quiero hablar con un asesor
"""


def test_android_roles_y_mensajes():
    c = leer(ANDROID, "chat-1")
    assert c.canal == "whatsapp"
    assert [t.rol for t in c.turnos] == ["cliente", "agente", "cliente", "agente", "cliente"]
    assert "no me han devuelto nada" in c.turnos[0].texto  # línea de continuación y mensajes seguidos
    assert all("Multimedia" not in t.texto for t in c.turnos)
    assert all("cifrados" not in t.texto for t in c.turnos)  # aviso del sistema
    assert c.inicio.hour == 10 and c.inicio.utcoffset().total_seconds() == -5 * 3600
    assert c.cliente_id == "Ana María Pérez"  # solo en memoria: la zona local lo seudonimiza


def test_iphone_12_horas_y_celular_como_autor():
    c = leer(IPHONE, "chat-2")
    assert c.inicio.hour == 21
    assert c.cliente_id == "+57 310 555 1234"
    assert c.turnos[1].t == pytest.approx(98.0)


def test_empresa_explicita():
    c = leer(ANDROID, "chat-1", empresa=["banco cordillera"])
    assert c.turnos[1].rol == "agente"


def test_nombres_de_participantes_no_salen():
    anon, _ = anonimizar_lote([leer(ANDROID, "chat-1"), leer(IPHONE, "chat-2")], SAL)
    for c in anon:
        assert c.cliente_id.startswith("h:")
        texto = " ".join(t.texto for t in c.turnos) + str(c.metadatos) + str([t.rol for t in c.turnos])
        for dato in ("Ana María", "Pérez", "310 555 1234", "1.144.123.456"):
            assert dato not in texto


def test_mismo_cliente_mismo_seudonimo_en_dos_chats():
    a, _ = anonimizar_lote([leer(IPHONE, "c1"), leer(IPHONE, "c2")], SAL)
    assert a[0].cliente_id == a[1].cliente_id


def test_deteccion_de_formato():
    assert es_exportacion(ANDROID) and es_exportacion(IPHONE)
    assert not es_exportacion("Agente: hola\nCliente: buenas")


def test_zip_y_carpeta(tmp_path):
    with zipfile.ZipFile(tmp_path / "Chat de WhatsApp con Ana.zip", "w") as z:
        z.writestr("_chat.txt", ANDROID)
        z.writestr("IMG-0001.jpg", b"\xff\xd8")
    (tmp_path / "otro.txt").write_text(IPHONE, encoding="utf-8")
    (tmp_path / "plano.txt").write_text("Agente: Buenos días\nCliente: Quiero cancelar", encoding="utf-8")
    convs = cargar_entradas([tmp_path])
    assert sorted(c.canal for c in convs) == ["voz", "whatsapp", "whatsapp"]


def test_zip_sin_chat(tmp_path):
    ruta = tmp_path / "vacio.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("foto.jpg", b"x")
    with pytest.raises(ValueError, match="no contiene"):
        cargar_entradas([ruta])


def test_exportar_chats_de_whatsapp(tmp_path):
    (tmp_path / "chat.txt").write_text(ANDROID, encoding="utf-8")
    r = exportar([tmp_path / "chat.txt"], tmp_path / "lote", SAL, "Prueba", empresa=["Banco Cordillera"])
    assert r.manifiesto["compuerta"]["aprobadas"] == 1
    salida = (tmp_path / "lote" / "conversaciones.jsonl").read_text(encoding="utf-8")
    assert "Ana" not in salida and "1.144.123.456" not in salida and "[CEDULA]" in salida
