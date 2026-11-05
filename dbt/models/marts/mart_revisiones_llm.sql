-- Segunda opinión con LLM sobre los casos que las reglas dejaron en «incierto»:
-- qué decidió el modelo y cuántos envíos se bloquearon por privacidad.
select
    lote_id,
    estado,
    resolucion_llm,
    count(*) as casos
from {{ source('raw', 'raw_revisiones_llm') }}
group by lote_id, estado, resolucion_llm
