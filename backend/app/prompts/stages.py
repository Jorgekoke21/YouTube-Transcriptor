"""Stage prompts for the AI pipeline (extraction → outline → writer → verification)."""

# v6: the extraction input no longer repeats the "Fragmento n de m" header.
PIPELINE_PROMPT_VERSION = "pipeline-v6"

EXTRACTION_PROMPT = """\
ETAPA A — EXTRACCIÓN.
Recibes UN fragmento de la transcripción. Cada línea empieza con su segundo de inicio entre corchetes: [123.4] texto.

Tarea: extrae únicamente la información presente realmente en ESTE fragmento.
- topics: temas tratados en el fragmento.
- items: cada idea relevante como elemento independiente (kind, certainty, text, tags, source_start, source_end).
  · text: paráfrasis fiel y autocontenida (que se entienda sin leer la transcripción). Sin conocimiento externo.
  · certainty = 'tentative' cuando el ponente duda o expresa una creencia/hipótesis.
  · source_start/source_end: segundos copiados de los marcadores [segundos] donde aparece la idea. No inventes.
- procedures: procedimientos con pasos en el orden en que se explican (si los hay).
Ignora saludos, despedidas, peticiones de suscripción, autopromoción y muletillas.
Si el fragmento no tiene contenido formativo, devuelve listas vacías.
"""

OUTLINE_PROMPT = """\
ETAPA B — ESQUEMA GLOBAL.
Recibes todas las ideas extraídas del vídeo, cada una con un identificador (id), tipo y tiempo.

Tarea: diseñar la estructura del documento. NO redactes todavía el documento.
- Determina los temas principales, el orden lógico, los capítulos y subsecciones.
- Ajusta el número de capítulos a la duración y densidad del vídeo: como orientación, un capítulo por cada
  3-6 minutos de contenido formativo (mínimo 2). Prefiere pocos capítulos sólidos a muchos capítulos finos.
- Agrupa ideas duplicadas o relacionadas en el mismo capítulo (aunque aparezcan en momentos distintos del vídeo).
  Un mismo tema NO debe repartirse entre varios capítulos ni repetirse con otro título.
- Asigna cada item_id relevante a exactamente un capítulo o subsección.
- omitted_item_ids: sólo ids sin valor formativo.
- title: título claro del documento basado en el contenido.
- summary: 2-4 frases que resuman el vídeo, usando sólo las ideas proporcionadas.
- key_points: 3-10 ideas clave, cada una con los item_ids que la respaldan.
- Ausencias: tú eres la única etapa que ve todo el vídeo. Si la ausencia de un elemento importante debe
  señalarse (p. ej. no se da ninguna regla de stop loss), indícalo en el purpose del capítulo donde encaje,
  empezando por "AUSENCIA:". Nunca marques como ausente algo que aparece en cualquier idea extraída.
Usa únicamente los ids proporcionados.
"""

WRITER_PROMPT = """\
ETAPA C — REDACCIÓN DE UN CAPÍTULO.
Recibes el esquema global del documento (para contexto) y el plan del capítulo que debes redactar, con las ideas
extraídas que lo respaldan (evidencias). Cada evidencia incluye su intervalo temporal en segundos.

Tarea: redacta SOLO este capítulo como una sección estructurada.
- Usa únicamente la información de las evidencias. No añadas nada que no esté en ellas: ni aclaraciones
  entre paréntesis, ni ejemplos propios, ni "etc.", ni consejos o consecuencias que el ponente no diga.
- Escribe directamente sobre el contenido. Nada de frases meta como "Este capítulo explica..." o
  "En esta sección veremos...".
- Mantén el título del capítulo (puedes pulirlo) y crea subsecciones cuando aporten claridad. Toda
  subsección debe tener título.
- No escribas tiempos ni segundos dentro del texto (nada de "(ver 318.6–346.4)"): usa source_ranges.
- Tipos de bloque: paragraph (text), bullet_list / numbered_list / checklist (items), table (table_headers +
  table_rows), quote (sólo para una frase breve y casi literal del ponente, como máximo una por capítulo),
  note (aclaraciones como la ausencia explícita de una regla). Deja vacíos los campos que no use cada bloque.
- source_ranges: el capítulo ya muestra su intervalo temporal. En los bloques úsalos sólo cuando el bloque
  remite a un momento concreto del vídeo (un ejemplo, una regla puntual); si no, deja la lista vacía.
  Copia siempre intervalos de las evidencias; nunca inventes tiempos.
- Refleja la incertidumbre del ponente (creo, normalmente, a veces) cuando la evidencia sea 'tentative',
  con lenguaje natural. No escribas en el texto las etiquetas internas de las evidencias ("tentativo",
  "afirmación", "recomendación", kind, certainty, ids).
- No generes secciones vacías ni relleno. No repitas contenido de otros capítulos del esquema.
- Notas de ausencia ("No se especifica una regla concreta sobre este punto en el vídeo."): añádelas SÓLO si
  el purpose del capítulo contiene "AUSENCIA:". Tú sólo ves este capítulo; otra parte del vídeo podría
  tratar ese punto.
"""

VERIFICATION_PROMPT = """\
ETAPA D — VERIFICACIÓN.
Recibes un capítulo redactado, las evidencias extraídas de la transcripción que lo respaldan y el fragmento
original de la transcripción (source_transcript, líneas "[segundos] texto") que cubre esas evidencias.

Tarea: revisar el capítulo contra las evidencias y la transcripción original, y devolver la versión corregida.
La transcripción original es la fuente de verdad: si una evidencia la malinterpreta (por ejemplo, un término
técnico cambiado por otro concepto), corrige el capítulo según la transcripción.
Detecta y corrige:
- afirmaciones sin soporte en las evidencias o posibles invenciones (conocimiento externo, cifras, reglas,
  indicadores, niveles, ejemplos o condiciones no mencionados) → elimínalas o corrígelas. Revisa con especial
  cuidado las aclaraciones entre paréntesis, los "etc." y los consejos añadidos: si no están en las
  evidencias, elimínalos;
- incertidumbre convertida en certeza → restaura el matiz;
- duplicados, contradicciones y secciones o bloques redundantes → elimínalos o fusiónalos; elimina también
  frases meta ("Este capítulo explica...");
- timestamps: el sistema los valida automáticamente contra las evidencias. Sólo reporta wrong_timestamp si
  un intervalo apunta a una parte del vídeo que trata OTRA idea; nunca por diferencias de segundos.
No añadas contenido nuevo. Si todo es correcto, devuelve el capítulo sin cambios y issues vacío.
En issues describe brevemente cada problema encontrado y la acción aplicada.
"""

CLEAN_TRANSCRIPT_PROMPT = """\
LIMPIEZA DE TRANSCRIPCIÓN.
Recibes un fragmento de transcripción. Cada línea empieza con su segundo de inicio: [123.4] texto.

Tarea: devolver el mismo contenido limpio, en orden:
- añade puntuación y mayúsculas, agrupa en párrafos coherentes;
- añade un heading breve sólo cuando cambie claramente el tema (si no, heading vacío);
- elimina moderadamente muletillas y repeticiones evidentes, saludos y peticiones de suscripción;
- NO resumas, NO reordenes, NO añadas información;
- cada párrafo lleva source_start (segundo del primer marcador que cubre) y source_end (segundo del último
  marcador que cubre, o posterior dentro del fragmento). Usa sólo segundos de los marcadores.
"""
