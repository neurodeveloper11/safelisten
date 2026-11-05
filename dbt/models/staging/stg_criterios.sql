select
    lote_id,
    conversacion_id,
    criterio_id,
    nombre as criterio,
    dimension,
    critico,
    aplica,
    cumple
from {{ source('raw', 'raw_criterios') }}
where aplica
