-- Fallas de criterios de cumplimiento (Ley 2300 de 2023, CRC, Ley 1480 de 2011) por sector y criterio.
select
    e.sector,
    c.criterio_id,
    c.criterio,
    count(*) as evaluadas,
    sum(case when c.cumple then 0 else 1 end) as fallas,
    round(avg(case when c.cumple then 0 else 1.0 end), 3) as tasa_falla
from {{ ref('stg_criterios') }} c
join {{ ref('stg_evaluaciones') }} e
  on c.lote_id = e.lote_id and c.conversacion_id = e.conversacion_id
where c.critico = 'cumplimiento'
group by e.sector, c.criterio_id, c.criterio
