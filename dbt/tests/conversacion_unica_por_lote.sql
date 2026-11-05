select lote_id, conversacion_id, count(*) as n
from {{ ref('stg_evaluaciones') }}
group by lote_id, conversacion_id
having count(*) > 1
