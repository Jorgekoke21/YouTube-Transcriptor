"""Stage prompts for consolidated (multi-video) documents.

They run on top of the per-video extractions (stage A): the transcripts are never sent again in full.
Changing any prompt here ⇒ bump COLLECTION_PROMPT_VERSION (it is part of the collection cache key).
"""

COLLECTION_PROMPT_VERSION = "collection-v1"

MULTI_SOURCE_RULES = """\
FUENTES MÚLTIPLES (obligatorio):
- Trabajas con ideas extraídas de VARIOS vídeos. Cada idea lleva "source" (V1, V2…) que identifica su vídeo.
- Nunca pierdas la procedencia: toda afirmación debe poder atribuirse a las ideas (ids) de las que sale.
- No atribuyas a un vídeo lo que dice otro. No mezcles en una sola regla lo que dicen fuentes distintas si no dicen lo mismo.
- Si dos fuentes expresan reglas, criterios o valores diferentes sobre el mismo punto, NO los fusiones ni los
  presentes como una sola regla, y no decidas cuál es mejor: represéntalos como diferencias entre fuentes.
- Que una fuente no mencione algo no es una contradicción ni una diferencia.
"""

CONSOLIDATION_PROMPT = """\
ETAPA B′ — CONSOLIDACIÓN DE VARIAS FUENTES.
Recibes la lista de vídeos (V1, V2…) y todas las ideas extraídas de ellos, cada una con id, source, tipo y tiempo.
No recibes las transcripciones completas: trabaja sólo con estas ideas.

Tarea: diseñar UN documento único que integre todas las fuentes. NO redactes todavía el documento.
- concept_groups: agrupa ideas equivalentes (mismo concepto, misma regla, mismo dato) para escribirlas una sola vez.
  · relation = 'agreement' cuando el mismo punto aparece en dos o más vídeos distintos (acuerdo entre fuentes);
    'repetition' cuando se repite dentro de un mismo vídeo; 'complementary' cuando son ideas del mismo tema
    que aportan detalles distintos y compatibles.
  · statement: formulación fiel de la idea compartida, usando sólo lo que dicen las ideas agrupadas.
  · Sólo grupos de 2 o más ideas. Una idea no debe estar en más de un grupo.
- differences: puntos en los que las fuentes dicen cosas distintas o incompatibles (p. ej. una coloca el stop
  bajo el mínimo y otra bajo la media). Por cada diferencia: topic y una position por fuente, con statement
  (lo que dice ESA fuente, fiel a sus ideas) e item_ids SOLO de esa fuente. Mínimo dos fuentes distintas.
  No inventes diferencias: matices compatibles o ideas que sólo aparecen en una fuente no son diferencias.
- chapters: estructura temática del documento (no un capítulo por vídeo): reorganiza los conceptos por lógica
  temática. Asigna cada item_id relevante a exactamente un capítulo o subsección; las ideas de un mismo
  concept_group van al mismo capítulo. Ajusta el número de capítulos a la cantidad de contenido; prefiere
  pocos capítulos sólidos. No crees un capítulo de diferencias: el sistema lo genera a partir de differences.
- omitted_item_ids: sólo ids sin valor formativo.
- title: título claro del documento conjunto. summary: 2-4 frases sobre el conjunto de fuentes.
- key_points: 3-10 ideas clave del conjunto, cada una con los item_ids que la respaldan.
- Ausencias: si la ausencia de un elemento importante debe señalarse, indícalo en el purpose del capítulo
  empezando por "AUSENCIA:" y di si falta en todas las fuentes. Nunca marques como ausente algo que aparece
  en cualquier idea extraída.
Usa únicamente los ids proporcionados.
"""

COLLECTION_WRITER_PROMPT = """\
ETAPA C′ — REDACCIÓN DE UN CAPÍTULO DEL DOCUMENTO CONSOLIDADO.
Recibes las fuentes (V1, V2…), el esquema global, el plan del capítulo, los grupos de ideas equivalentes que
le afectan (concept_groups) y las evidencias del capítulo, cada una con su source.

Tarea: redacta SOLO este capítulo como una sección estructurada que integre las fuentes.
- Usa únicamente la información de las evidencias. No añadas nada que no esté en ellas.
- Ideas equivalentes (mismo concept_group): escríbelas UNA sola vez y cita en evidence_ids todas las ideas del
  grupo (así el lector ve todas las fuentes que lo dicen). Si es un acuerdo entre fuentes puedes decir que
  varias fuentes coinciden, sin nombrar vídeos que no estén en las evidencias.
- evidence_ids es obligatorio en cada bloque: los ids de TODAS las evidencias que respaldan el bloque, y sólo
  esos. Un bloque sin evidencias será eliminado (salvo notas de ausencia). Si los elementos de una lista
  proceden de fuentes distintas y la procedencia importa, divide la lista en bloques.
- Si el texto atribuye algo a una fuente concreta, las evidencias citadas deben ser de esa fuente.
- Los puntos listados en differences_handled_elsewhere se presentan en un capítulo aparte: no los trates aquí.
- Escribe directamente sobre el contenido, sin frases meta. Toda subsección debe tener título.
- No escribas tiempos, segundos, ids ni referencias V1/V2 dentro del texto: los enlaces a cada vídeo se
  generan automáticamente a partir de evidence_ids.
- Tipos de bloque: paragraph (text), bullet_list / numbered_list / checklist (items), table (table_headers +
  table_rows), quote (frase breve casi literal de un ponente, como máximo una por capítulo), note (aclaraciones
  como la ausencia explícita de una regla). Deja vacíos los campos que no use cada bloque.
- Refleja la incertidumbre del ponente cuando la evidencia sea 'tentative'. No escribas etiquetas internas.
- No generes secciones vacías ni relleno. No repitas contenido de otros capítulos del esquema.
- Notas de ausencia: SÓLO si el purpose del capítulo contiene "AUSENCIA:".
"""

COLLECTION_VERIFICATION_PROMPT = """\
ETAPA D′ — VERIFICACIÓN DE UN CAPÍTULO CONSOLIDADO.
Recibes un capítulo redactado (cada bloque con sus evidence_ids), las evidencias (cada una con su source) y los
fragmentos originales de la transcripción de cada vídeo (source_transcripts, por fuente, líneas "[segundos] texto").

Tarea: revisar el capítulo contra las evidencias y las transcripciones originales y devolver la versión corregida.
Las transcripciones originales son la fuente de verdad.
Detecta y corrige:
- afirmaciones sin soporte o posibles invenciones (conocimiento externo, cifras, reglas, niveles, ejemplos) →
  elimínalas o corrígelas;
- incertidumbre convertida en certeza → restaura el matiz;
- atribución incorrecta: un bloque que cita evidencias que no respaldan su texto, o que atribuye a una fuente
  lo que dice otra → corrige evidence_ids (sólo ids de las evidencias recibidas) o el texto;
- fuentes mezcladas: reglas distintas de fuentes distintas fusionadas como si fueran una → sepáralas,
  atribuyendo cada una a su fuente, sin decidir cuál es mejor;
- bloques sin evidence_ids → añade las evidencias que los respaldan o elimina el bloque;
- duplicados y bloques redundantes → fusiónalos citando todas sus evidencias; elimina frases meta.
No añadas contenido nuevo. Si todo es correcto, devuelve el capítulo sin cambios y issues vacío.
En issues describe brevemente cada problema encontrado y la acción aplicada.
"""
