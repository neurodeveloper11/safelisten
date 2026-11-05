-- Falla si algún identificador de cliente llegó sin seudonimizar (debe empezar por «h:»).
select conversacion_id, lote_id
from {{ ref('stg_evaluaciones') }}
where cliente_hash is not null and cliente_hash not like 'h:%'
