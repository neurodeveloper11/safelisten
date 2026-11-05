select
    lote_id,
    organizacion,
    cast(replace(substr(creado_en, 1, 19), 'T', ' ') as timestamp) as creado_en_utc,
    cast(replace(substr(recibido_en, 1, 19), 'T', ' ') as timestamp) as recibido_en_utc,
    conversaciones,
    reemplazos_pii,
    en_cuarentena,
    sha256,
    version_datashield,
    version_alometro
from {{ source('raw', 'raw_lotes') }}
