-- Por asesor: su puntaje y los 3 criterios que más falla (insumo para coaching, no para sanción).
with por_criterio as (
    select
        e.agente_id,
        c.criterio_id,
        c.criterio,
        count(*) as evaluadas,
        sum(case when c.cumple then 0 else 1 end) as fallas
    from {{ ref('stg_criterios') }} c
    join {{ ref('stg_evaluaciones') }} e
      on c.lote_id = e.lote_id and c.conversacion_id = e.conversacion_id
    where e.agente_id is not null
    group by e.agente_id, c.criterio_id, c.criterio
),

ranqueado as (
    select
        *,
        row_number() over (partition by agente_id order by fallas desc, criterio_id) as orden
    from por_criterio
    where fallas > 0
),

resumen as (
    select agente_id, count(*) as conversaciones, round(avg(puntaje), 1) as puntaje_promedio
    from {{ ref('stg_evaluaciones') }}
    where agente_id is not null
    group by agente_id
)

select
    r.agente_id,
    r.conversaciones,
    r.puntaje_promedio,
    k.orden as prioridad,
    k.criterio_id,
    k.criterio,
    k.fallas,
    k.evaluadas
from resumen r
left join ranqueado k on r.agente_id = k.agente_id and k.orden <= 3
