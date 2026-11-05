-- Tablero diario por canal: lo que ve el gerente de servicio.
select
    fecha,
    canal,
    count(*) as conversaciones,
    round(avg(puntaje), 1) as puntaje_promedio,
    round(avg(case when resolucion = 'resuelto' then 1.0 else 0 end), 3) as tasa_resolucion,
    round(avg(case when abandono then 1.0 else 0 end), 3) as tasa_abandono,
    round(avg(case when esfuerzo >= 5 then 1.0 else 0 end), 3) as tasa_esfuerzo_alto,
    round(avg(case when critico_cliente then 1.0 else 0 end), 3) as tasa_critico_cliente,
    round(avg(case when critico_cumplimiento then 1.0 else 0 end), 3) as tasa_critico_cumplimiento,
    sum(case when peticiones_humano > 0 then 1 else 0 end) as pidieron_humano,
    sum(case when peticiones_humano > 0 and humano_atendido then 1 else 0 end) as humano_atendido,
    round(avg(case when hubo_bot then (case when bucle_bot then 1.0 else 0 end) end), 3) as tasa_bucle_bot
from {{ ref('stg_evaluaciones') }}
group by fecha, canal
