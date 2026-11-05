# SafeListen

[![CI](https://github.com/neurodeveloper11/safelisten/actions/workflows/ci.yml/badge.svg)](https://github.com/neurodeveloper11/safelisten/actions/workflows/ci.yml)
[![Licencia: MIT](https://img.shields.io/badge/licencia-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)

**Escuche cada conversación sin exponer a nadie.** SafeListen audita la calidad y el cumplimiento de las conversaciones de call center y chatbots (voz transcrita, chat, WhatsApp) en Colombia y Latinoamérica. Los datos personales se anonimizan **dentro de la empresa**, antes de que nada salga a la nube.

🌐 **Sitio y servicio:** https://neurodeveloper11.github.io/safelisten/ · 📄 [Informe de ejemplo](https://neurodeveloper11.github.io/safelisten/ejemplo/informe.html)

Se construye sobre dos librerías propias:

* [**DataShield**](https://github.com/neurodeveloper11/pii-shield-es): detección y anonimización de datos personales de Colombia/LatAm.
* [**Alómetro**](https://github.com/neurodeveloper11/alometro): 40 criterios auditables y 16 indicadores de calidad de atención.

Ambas se instalan como dependencias fijadas a un commit; este repositorio no copia su código.

## Arquitectura

```
 EN LA EMPRESA (sin red)                      NUBE (Google Cloud)                     EQUIPO
 ┌─────────────────────────────┐   lote    ┌──────────────────────────────┐       ┌──────────────┐
 │ Excel / CSV / WhatsApp /    │ anónimo + │ API en Cloud Run             │       │ Tableros     │
 │ .srt / .vtt                 │ manifiesto│  1. verifica SHA-256         │ dbt   │ (BigQuery /  │
 │  → DataShield + reglas de   │ ────────► │  2. repite la compuerta      │ ────► │  Looker      │
 │    diálogo                  │           │  3. Alómetro califica        │       │  Studio)     │
 │  → seudónimo HMAC del       │           │  4. carga BigQuery (raw_*)   │       │              │
 │    cliente                  │           │  [opcional] segunda opinión  │       │              │
 │  → compuerta de fugas       │           │  con LLM solo en casos       │       │              │
 │    (2 detectores)           │           │  inciertos                   │       │              │
 └─────────────────────────────┘           └──────────────────────────────┘       └──────────────┘
```

| Módulo | Zona | Qué hace |
|---|---|---|
| `edge.py` | Empresa | Anonimiza con DataShield y agrega dos reglas de diálogo (ver abajo). Seudonimiza `cliente_id` con HMAC-SHA256 y una sal que no sale de la empresa. Deja pasar solo los metadatos de una lista blanca. |
| `gate.py` | Ambas | Compuerta de fugas con dos detectores independientes: DataShield otra vez y un detector propio de expresiones regulares. En la empresa, lo dudoso queda en cuarentena; en la nube, el lote completo se rechaza. |
| `manifest.py` | Ambas | Evidencia por lote: conteos por tipo de dato, resultado de la compuerta, versiones y SHA-256. |
| `score.py` / `warehouse.py` | Nube | Califica con Alómetro y carga en DuckDB (local) o BigQuery, de forma idempotente por lote. |
| `review.py` | Nube | Segunda opinión con un modelo de lenguaje, solo para resoluciones `incierto`, con salida JSON de esquema fijo. Antes de enviar, repite la compuerta. |
| `api.py` | Nube | FastAPI para Cloud Run: `POST /v1/lotes` con token Bearer y `GET /salud`. |
| `flows.py` | Empresa | Flujo nocturno con Prefect (exportar → enviar). |
| `dbt/` | Nube | Staging y marts: indicadores diarios, cumplimiento por norma, coaching por asesor y evidencia de privacidad. Corre igual en DuckDB y en BigQuery. |
| `infra/terraform/` | Nube | Cloud Run, BigQuery, Artifact Registry, Secret Manager y una cuenta de servicio con mínimo privilegio. |

### Conversaciones, no documentos sueltos

En atención al cliente el cliente dicta su cédula varios turnos después de que se la piden («¿otra vez?» → «es necesario» → «12.635.880») y los asesores llaman a la persona por su nombre de pila («Entiendo su preocupación, Jorge, …»). SafeListen le pasa a DataShield los tres turnos anteriores como contexto (`contexto=`, desde DataShield 0.5.1) y agrega una segunda capa propia: nombres de pila de la lista de DataShield (los que también son palabras comunes, como Luz o Rosa, solo con contexto) y recorte de cualquier entidad que termine a mitad de palabra.

Estas mejoras de DataShield (contexto de diálogo, vocativos, presentación del asesor y el arreglo de entidades cortadas a mitad de palabra) salieron de las pruebas de SafeListen y se incorporaron a la librería.

## Verificación

Lo que se comprobó, y que las pruebas automáticas repiten en cada cambio:

* **Cero fugas:** sobre 3.000 conversaciones sintéticas (5 semillas × 2 periodos) no queda ninguna cédula, nombre de cliente ni id original, y la compuerta no pone nada en cuarentena.
* **La anonimización no altera la medición:** cada criterio de cada conversación, el puntaje y los 16 indicadores de Alómetro dan exactamente igual con y sin datos personales. (Una primera versión mostraba una diferencia: Alómetro contaba dos veces que el cliente dictara de nuevo su cédula. Se corrigió en Alómetro.)
* **La nube no confía en la empresa:** un lote con manifiesto válido pero con un correo en el texto se rechaza completo y no se guarda nada.
* **Integridad:** si se altera un solo byte del archivo, el SHA-256 no coincide y el lote se rechaza.
* **dbt:** 18 modelos y pruebas de datos, incluida una que falla si algún cliente llega sin seudónimo.

```bash
pip install -e ".[dev]"
pytest -q                                     # 28 pruebas
safelisten demo -o demo -n 400                # flujo completo con datos sintéticos
cd dbt && SAFELISTEN_DUCKDB=../demo/safelisten.duckdb dbt build --profiles-dir .
```

En CI también se valida Terraform (`fmt` y `validate`) y se construye la imagen Docker, comprobando que responde en `/salud`.

## Uso

```bash
# En la empresa (una sola vez): la sal se guarda en un gestor de secretos local
safelisten sal > sal.txt

# Cada día: anonimizar y preparar el lote
safelisten exportar exportacion_whatsapp.xlsx llamadas/ -o lote/ --org "Mi Empresa" --sal-archivo sal.txt

# Enviar a la API
safelisten enviar lote/ --url https://safelisten-api-xxxx.a.run.app --token "$SAFELISTEN_TOKEN"

# O procesar localmente en DuckDB (pruebas, operaciones sin nube)
safelisten procesar lote/ --duckdb safelisten.duckdb
```

Formatos de entrada: los mismos de Alómetro (JSONL, CSV con `,` `;` o tabulador, Excel, texto `Agente:`/`Cliente:`, subtítulos `.srt`/`.vtt` de motores de voz a texto).

## Despliegue en Google Cloud

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # proyecto e imagen
terraform init && terraform apply
openssl rand -hex 32 | gcloud secrets versions add safelisten-token --data-file=-
gcloud builds submit --tag "$(terraform output -raw repositorio_imagenes)/api:0.1.0" ../..
```

La región por defecto es `southamerica-east1` (São Paulo). Cloud Run escala a cero, así que sin tráfico el costo es mínimo.

Para la segunda opinión con LLM: `pip install ".[llm]"`, configure `ANTHROPIC_API_KEY` y, si quiere otro modelo, `SAFELISTEN_MODELO_LLM`. Esta integración se probó con un cliente simulado; antes de usarla en una operación, mida su acuerdo con analistas humanos (`alometro calibrar`).

## Límites

* No es asesoría jurídica. Los criterios resumen la Ley 2300 de 2023, la Ley 1480 de 2011, la Ley 1581 de 2012 y resoluciones de la CRC; verifique el texto vigente.
* La anonimización es heurística: por eso hay dos detectores, cuarentena y rechazo en la nube. Aun así, ningún sistema garantiza el 100 %.
* Las cifras del sitio vienen de datos sintéticos. En un piloto se miden con conversaciones reales y se calibran contra analistas.
* El módulo de BigQuery y el flujo de Prefect se probaron con dobles de prueba y en CI, no contra un proyecto real de GCP.

## Autor

**Fabio Ignacio Torres Benítez**, psicólogo con más de 10 años en psicometría y riesgo psicosocial, e ingeniero de datos e IA. [LinkedIn](https://www.linkedin.com/in/fabio-torres-39364b258) · [Portafolio](https://neurodeveloper11.github.io/portfolio/)

Licencia [MIT](LICENSE).
