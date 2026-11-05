-- Evidencia para el oficial de protección de datos: qué se anonimizó en cada lote.
select
    lote_id,
    organizacion,
    creado_en_utc,
    recibido_en_utc,
    conversaciones,
    reemplazos_pii,
    en_cuarentena,
    sha256,
    version_datashield,
    version_alometro
from {{ ref('stg_lotes') }}
