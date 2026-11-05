"""La segunda opinión con LLM se prueba con un cliente falso: nunca llama a la API real."""

import json
from types import SimpleNamespace

from alometro import evaluar
from alometro.modelos import Conversacion, Turno

from safelisten import review


class ClienteFalso:
    def __init__(self, respuesta=None, stop_reason="end_turn"):
        self.llamadas = []
        self.respuesta = respuesta or {"resolucion": "en_tramite", "turno_evidencia": 1, "justificacion": "Radicó."}
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.llamadas.append(kwargs)
        bloque = SimpleNamespace(type="text", text=json.dumps(self.respuesta))
        return SimpleNamespace(stop_reason=self.stop_reason, content=[bloque])


def _conv(texto_cliente: str, cliente_id: str = "h:abc") -> Conversacion:
    return Conversacion(
        id="r-1",
        cliente_id=cliente_id,
        turnos=[
            Turno("cliente", texto_cliente),
            Turno("agente", "Le dejo el radicado [NUMERO], respuesta en 15 días."),
        ],
    )


def test_envia_solo_texto_anonimo_con_esquema():
    conv = _conv("Me cobraron doble, [NOMBRE] me dijo que lo revisaban")
    cliente = ClienteFalso()
    r = review.revisar(conv, evaluar(conv), cliente)
    assert r.estado == "ok" and r.resolucion_llm == "en_tramite"
    llamada = cliente.llamadas[0]
    assert llamada["model"] == review.MODELO
    assert llamada["output_config"]["format"]["schema"] == review.ESQUEMA
    assert "[NOMBRE]" in llamada["messages"][0]["content"]


def test_bloquea_si_quedan_datos_personales():
    conv = _conv("mi correo es ana@correo.com")
    cliente = ClienteFalso()
    r = review.revisar(conv, evaluar(conv), cliente)
    assert r.estado == "bloqueada_por_privacidad"
    assert cliente.llamadas == []


def test_negativa_del_modelo():
    conv = _conv("Me cobraron doble")
    r = review.revisar(conv, evaluar(conv), ClienteFalso(stop_reason="refusal"))
    assert r.estado == "rechazada_por_modelo" and r.resolucion_llm is None
