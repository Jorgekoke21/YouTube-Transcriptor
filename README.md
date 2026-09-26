# YouTube → Document

Pega la URL de un vídeo de YouTube que tenga transcripción y obtén un **documento de estudio estructurado, fiel al vídeo y descargable** (Markdown, DOCX, PDF y TXT), con referencias temporales clicables al momento exacto del vídeo.

> **La aplicación depende de que exista una transcripción de YouTube accesible. Si no existe, el vídeo no se procesa.**
> No descarga vídeo ni audio, no usa Whisper y no intenta generar una transcripción por su cuenta.

---

## 1. Requisitos

- **Python 3.11+** (probado con 3.14)
- **Node.js 20+** (probado con 22) y npm
- Una **API key de OpenAI** (solo para generar documentos; la inspección y la transcripción funcionan sin ella)

No se necesita Docker, Redis ni ningún servicio externo aparte de YouTube y OpenAI.

## 2. Instalación del backend

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements-dev.txt     # o requirements.txt si no vas a ejecutar tests/lint
```

## 3. Instalación del frontend

```bash
cd frontend
npm install
```

## 4. Configuración (`.env`)

Copia `.env.example` a `.env` **en la raíz del repositorio** y rellénalo:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna
OPENAI_REASONING_EFFORT=high
OPENAI_REASONING_MODE=standard
BACKEND_PORT=8000
FRONTEND_PORT=5173
```

| Variable | Por defecto | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | — | **Obligatoria para generar documentos.** Solo la lee el backend. |
| `OPENAI_MODEL` | — (**obligatoria** si `AI_PROVIDER=openai`) | Modelo de todas las etapas, p. ej. `gpt-5.6-luna`. No hay modelo por defecto en el código. |
| `OPENAI_REASONING_EFFORT` | `high` | Esfuerzo de razonamiento: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` (no todos los modelos admiten todos). |
| `OPENAI_REASONING_MODE` | `standard` | Modo de ejecución del razonamiento: `standard` o `pro`. |
| `BACKEND_PORT` / `FRONTEND_PORT` | `8000` / `5173` | Puertos. El proxy de Vite apunta a `BACKEND_PORT`. |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Orígenes permitidos (nunca `*` por defecto). |
| `TRANSCRIPT_LANGUAGES` | `es,en` | Orden de preferencia de idioma de la transcripción. |
| `DATA_DIR` | `data/` (raíz del repo) | Dónde se guardan la base SQLite y los documentos. |
| `AI_ENABLE_VERIFICATION` | `true` | Activa la etapa D (verificación). Desactivarla reduce el coste a la mitad aprox. |
| `MAX_VIDEOS_PER_BATCH` | `20` | Máximo de vídeos distintos por lote multi-URL (los duplicados no cuentan). Si se supera: error `BATCH_TOO_LARGE`. |
| `TRANSCRIPT_PROVIDER` | `youtube` | `fixture` = transcripción de ejemplo local (desarrollo sin red). |
| `AI_PROVIDER` | `openai` | `fake` = IA simulada determinista (desarrollo sin coste). |
| `YOUTUBE_PROXY_PROVIDER` | `none` | Proxy opcional **solo** para las peticiones de transcripción a YouTube: `none` o `webshare`. Ver [Proxy para YouTube](#proxy-para-youtube-opcional). |
| `WEBSHARE_PROXY_USERNAME` / `WEBSHARE_PROXY_PASSWORD` | *(vacío)* | Credenciales de Webshare. Obligatorias si `YOUTUBE_PROXY_PROVIDER=webshare`; ignoradas en otro caso. |
| `WEBSHARE_PROXY_LOCATIONS` | *(vacío)* | Países de salida del proxy separados por comas (p. ej. `es,de,fr`). Vacío = cualquiera. |

`.env` está en `.gitignore`. La API key nunca se envía al frontend, ni aparece en respuestas HTTP ni en los logs.

> **Nota:** una variable de entorno del sistema tiene prioridad sobre el archivo `.env`. Si ya tienes `OPENAI_API_KEY` definida en el sistema, será la que se use.

### Modelo y razonamiento

`OPENAI_MODEL`, `OPENAI_REASONING_EFFORT` y `OPENAI_REASONING_MODE` se leen de las variables de entorno (o del `.env`) y se centralizan en `app/core/config.py`. **Todas** las llamadas a la Responses API —extracción, esquema global, redacción, verificación y transcripción limpia— pasan por el mismo cliente (`OpenAIStructuredLLM`, creado por `OpenAIProcessor`) y envían:

```python
model=settings.openai_model
reasoning={"effort": settings.openai_reasoning_effort, "mode": settings.openai_reasoning_mode}
```

- Los valores se **validan al arrancar**: un esfuerzo o modo no reconocido, o `OPENAI_MODEL` vacío con `AI_PROVIDER=openai`, impiden iniciar el backend con un mensaje claro.
- Al arrancar se registra en el log (sin la API key):
  ```text
  OpenAI configuration:
  model=gpt-5.6-luna
  reasoning_effort=high
  reasoning_mode=standard
  api_key=configured
  ```
- `GET /api/health` devuelve el modelo y el `reasoning` efectivos.
- Modelo y reasoning forman parte de la clave de caché: si cambias cualquiera de los tres valores, no se reutilizan documentos generados con la configuración anterior.

### Proxy para YouTube (opcional)

En local la IP es residencial y YouTube responde con normalidad. Desde proveedores cloud (Render, AWS, GCP, Azure…) YouTube suele responder con `RequestBlocked` / `IpBlocked` porque la petición sale de una IP de datacenter; la API lo devuelve como `TRANSCRIPT_FETCH_FAILED` y el log del backend lo marca como `youtube_ip_blocked`.

Para esos despliegues se puede enrutar **solo** el `YouTubeTranscriptProvider` a través de un proxy residencial rotativo, usando el soporte oficial de `youtube-transcript-api`:

```env
YOUTUBE_PROXY_PROVIDER=webshare
WEBSHARE_PROXY_USERNAME=...
WEBSHARE_PROXY_PASSWORD=...
WEBSHARE_PROXY_LOCATIONS=es,de,fr   # opcional
```

- Es **opcional**: con `YOUTUBE_PROXY_PROVIDER=none` (por defecto) se usa `YouTubeTranscriptApi()` sin proxy, exactamente como antes.
- Con `webshare` se crea `YouTubeTranscriptApi(proxy_config=WebshareProxyConfig(...))`. Hacen falta proxies **"Residential"** de Webshare (los planes "Proxy Server"/"Static Residential" y el gratuito no funcionan de forma fiable con YouTube).
- Si falta el usuario o la contraseña, el backend **no arranca** y muestra qué variable falta.
- OpenAI y los metadatos (oEmbed) **no** pasan por el proxy.
- Las credenciales solo las lee el backend: no aparecen en el frontend, en respuestas HTTP, en logs, en `processed.json` ni en claves de caché. `GET /api/health` solo indica `transcript_proxy: "none" | "webshare"`.

## 5. Cómo ejecutar

Dos terminales:

```bash
# Terminal 1 — backend (desde backend/, con el venv activado)
uvicorn app.main:create_app --factory --reload --port 8000

# Terminal 2 — frontend (desde frontend/)
npm run dev
```

Abre <http://localhost:5173>. El frontend llama al backend a través del proxy `/api` de Vite.

**Modo desarrollo sin YouTube ni OpenAI** (para trabajar en la UI o en el pipeline):

```bash
# Windows PowerShell
$env:TRANSCRIPT_PROVIDER="fixture"; $env:AI_PROVIDER="fake"; uvicorn app.main:create_app --factory --reload
# macOS / Linux
TRANSCRIPT_PROVIDER=fixture AI_PROVIDER=fake uvicorn app.main:create_app --factory --reload
```

Con `fixture`, cualquier ID de vídeo devuelve la transcripción de ejemplo (`backend/tests/fixtures/`); los IDs que empiezan por `NOTRANSCR` simulan un vídeo sin transcripción y los que empiezan por `DISABLED`, transcripciones desactivadas. Puedes combinar `AI_PROVIDER=fake` con transcripciones reales de YouTube.

## 6. Configurar OpenAI

1. Crea una API key en <https://platform.openai.com/api-keys>.
2. Ponla en `OPENAI_API_KEY` del `.env` (raíz del repo) y define `OPENAI_MODEL` (y, si quieres, `OPENAI_REASONING_EFFORT` / `OPENAI_REASONING_MODE`).
3. Reinicia el backend. El log de arranque y `GET /api/health` muestran la configuración efectiva y `"ai_configured": true`.

Si falta la clave, la inspección de vídeos funciona igualmente y al generar se muestra: *"El procesamiento con IA no está configurado. Añade OPENAI_API_KEY…"*.

**Coste orientativo** (medido con `gpt-5-mini` sin `reasoning` explícito, vídeo de 6 min / ~1.000 palabras, apuntes completos con verificación): 8–10 llamadas, ~20–28k tokens de entrada y ~22–33k de salida, 2–3 minutos. Con otro modelo o con `effort=high` el consumo y el tiempo pueden ser bastante mayores; aún no se ha medido con `gpt-5.6-luna`. El uso real se guarda por documento (ver §7, `AIUsage`).

## 7. Arquitectura

```text
/
├── backend/
│   ├── app/
│   │   ├── api/routes.py            Endpoints (capa fina: validar y delegar)
│   │   ├── core/                    config (Settings), errores (códigos), container (DI manual)
│   │   ├── models/                  Transcript/TranscriptSegment, documento estructurado, AIUsage
│   │   ├── schemas/api.py           Esquemas HTTP
│   │   ├── prompts/                 Prompts versionados: base, etapas y modos
│   │   ├── repositories/            SQLite (sqlite3 estándar)
│   │   ├── services/
│   │   │   ├── transcripts/         TranscriptProvider → YouTubeTranscriptProvider / Fixture
│   │   │   ├── ai/                  AIProcessor → OpenAIProcessor, pipeline, chunking, timestamps
│   │   │   ├── documents/           Renderers Markdown / DOCX / PDF / TXT
│   │   │   ├── metadata.py          Título/canal/miniatura vía oEmbed (sin API key, best-effort)
│   │   │   ├── cache.py             Fingerprint de caché
│   │   │   └── document_service.py  Orquestación URL → documento
│   │   └── utils/                   IDs de YouTube, timestamps, nombres de archivo
│   └── tests/
├── frontend/src/                    React + Vite + TS + Tailwind (router por hash, sin dependencias extra)
└── data/                            SQLite + documentos generados (ignorado por git)
```

### Flujo

```text
URL → validar y extraer video_id → metadatos (oEmbed) → listar transcripciones → elegir según preferencia
    → descargar segmentos con timestamps → normalizar → fingerprint de caché
    → (caché) reutilizar documento  |  pipeline IA → documento estructurado → MD/DOCX/PDF/TXT
```

El procesamiento se ejecuta en segundo plano; el frontend consulta `GET /api/documents/{id}` y muestra los **estados reales** del backend (`fetching_transcript → processing [extracting / outlining / writing / verifying] → generating_files → completed | failed`), con contadores reales (fragmento *n* de *m*, sección *n* de *m*), sin porcentajes inventados.

### Pipeline de IA (preparado para vídeos largos)

1. **Chunking** temporal: agrupa segmentos consecutivos (~1.400 palabras) y corta en fin de frase o pausa, nunca a mitad de segmento. Cada chunk conoce `start_time`, `end_time`, sus segmentos y su texto; al modelo se le envía cada línea como `[segundos] texto`.
2. **A · Extracción** (por chunk, en paralelo): ideas presentes en el fragmento como JSON estructurado (tipo, certeza *afirmado/tentativo*, texto, segundos de inicio/fin).
3. **B · Esquema global**: capítulos, orden, agrupación de duplicados, ideas clave y ausencias relevantes. No redacta.
4. **C · Redacción** por capítulo (en paralelo) usando solo las evidencias asignadas.
5. **D · Verificación** por capítulo contra las evidencias **y el fragmento original de la transcripción**: elimina afirmaciones sin soporte, restaura matices, fusiona duplicados.

Todas las etapas usan la **Responses API** con **Structured Outputs** (`responses.parse(text_format=ModeloPydantic)`, JSON Schema estricto), `store=False`, sin `previous_response_id` (sin estado) y **sin herramientas**: el modelo no navega por Internet. Reintentos: los de red/429/5xx los gestiona el SDK; las salidas vacías o incompletas se reintentan una vez.

**Salvaguardas deterministas** (no dependen del modelo):
- Los timestamps se recortan al chunk de origen, se ajustan a límites reales de segmentos y se descartan si no se solapan con la evidencia del capítulo. **Un timestamp inventado nunca llega al documento.**
- Las ideas que el esquema olvida se reasignan al capítulo más cercano en el tiempo (no se pierden); las ideas clave sin evidencia se eliminan.
- Se eliminan bloques vacíos, subsecciones sin título y segundos escritos dentro del texto.

### Varios vídeos a la vez (multi-URL)

Pega varias URLs en la caja de texto, una por línea (con una sola línea la app se comporta exactamente igual que antes). Se eliminan las líneas vacías, se normalizan las URLs (mismas variantes: `watch`, `youtu.be`, `shorts`, `live`, `embed`…) y se deduplican por `youtube_id`. **ANALIZAR VÍDEOS** inspecciona cada vídeo de forma independiente (miniatura, título, canal, duración si ya se conoce, idioma y tipo de transcripción, error individual): un vídeo inválido o sin transcripción nunca impide analizar los demás. Después se marcan los vídeos a procesar y se elige la salida:

| `output_mode` | Resultado |
|---|---|
| `individual` | Un `ProcessedDocument` por vídeo, generado con **exactamente** el flujo de una URL (`DocumentService.create` + `run`, misma caché) |
| `consolidated` | Un único `ConsolidatedDocument` que integra todos los vídeos |
| `both` | Primero los individuales (llenan la caché de extracciones) y después el consolidado |

**Pipeline consolidado** (`services/ai/collection.py`), sin reenviar las transcripciones completas:

```text
Transcript → A · extracción por vídeo (caché) → pool de evidencias con procedencia (V1.c0-i3 …)
  → B′ · consolidación: conceptos equivalentes, coincidencias, diferencias entre fuentes y esquema temático
  → C′ · redacción por capítulo (el modelo cita ids de evidencia, nunca escribe tiempos ni vídeos)
  → D′ · verificación contra las evidencias y los fragmentos originales de cada transcripción
  → resolución determinista de la procedencia → MD / DOCX / PDF / TXT
```

- **Procedencia**: cada evidencia conoce `youtube_id`, título, `source_start`, `source_end` y URL. El código resuelve cada id citado a su vídeo y su intervalo; los ids inventados o ajenos al capítulo se descartan y un bloque sin evidencias válidas se elimina (fidelidad > completitud). Cada enlace abre el vídeo correcto en ese segundo (`Divergencias con RSI · 04:32 ↗`). `processed.json` incluye `citations` con la procedencia completa de cada afirmación citada.
- **Diferencias**: si dos vídeos dan reglas distintas, se genera la sección *Diferencias entre enfoques* con un párrafo por fuente. La construye el código (cada posición solo puede citar evidencias de su propio vídeo) y, si la verificación intentara fusionar las fuentes, se conserva la versión atribuida. No se decide cuál es mejor.
- **Coincidencias**: los conceptos equivalentes se escriben una sola vez citando todas sus fuentes; los acuerdos entre vídeos distintos (comprobado por código) se listan en *Coincidencias entre fuentes*.
- **Una colección es un conjunto de vídeos sin orden**: `A,B,C`, `C,A,B`, `B,C,A` y `A,A,B,C` son la misma colección (misma identidad, misma clave de caché, mismo documento). Internamente se usa un orden canónico (`youtube_id` ordenado) para hashes, IDs de evidencia (`V1`, `V2`… se asignan en ese orden) y comparación; el orden en que se pegaron las URLs solo se guarda como metadato (`collection_videos.position`). La IA decide el orden temático del documento, y la lista de fuentes visible sigue el orden de **primera aparición** de cada vídeo en el documento.
- `clean_transcript` solo admite salida individual. El consolidado necesita al menos 2 vídeos con transcripción (si uno falla durante el proceso, se continúa con el resto).

### Prompts versionados

`app/prompts/`: `base.py` (prompt de fidelidad), `stages.py` (A–D y transcripción limpia), `modes.py` (un bloque por modo). Cada familia tiene versión (`base-v2`, `pipeline-v6`, `full-notes-v1`, `trading-v3`…). La versión usada se guarda con cada documento y forma parte de la clave de caché. **Si cambias un prompt, sube su versión.**

### Caché / idempotencia

```text
cache_key = SHA256(transcript_hash + document_type + output_language + prompt_version + model_config)
```

Si ya existe un documento completado con la misma clave, se reutiliza sin llamar a la IA. `force_regenerate: true` (botón *"Regenerar sin caché"* en la UI) lo ignora (también la caché de extracciones).

Además hay dos cachés más:

```text
extracción (etapa A, por vídeo) = SHA256(youtube_id + transcript_hash + proveedor + modelo + reasoning
                                         + versiones de prompts (base, pipeline, modo) + idioma + chunking)
consolidado                     = SHA256(ids de vídeo ORDENADOS con su transcript_hash + document_type + idioma
                                         + versiones de prompts (+ collection-vN) + modelo + reasoning + flags)
```

La caché de extracciones la comparten los documentos individuales y los consolidados: un vídeo ya procesado individualmente no se vuelve a extraer para el consolidado (y viceversa). Se guarda en `data/videos/VIDEO_ID/extractions/<clave>.json`, indexada en la tabla `extractions`.

### Persistencia

SQLite (`data/app.db`), tablas `videos` y `documents` (con estado, rutas, clave de caché, versión de prompts, modelo, uso de tokens, progreso y error); `collections` (lote multi-URL: modo de salida, estado, documento consolidado, clave de caché…), `collection_videos` (relación colección–vídeo con la posición en que se pegó, documento individual, estado y error por vídeo) y `extractions` (caché de extracciones por vídeo). Las tablas nuevas se crean solas al arrancar sobre una base existente. Los consolidados se guardan en `data/collections/{id}/`. Archivos:

```text
data/videos/VIDEO_ID/
  metadata.json
  transcript.json
  documents/{id}-{tipo}-{idioma}/
    processed.json   ← representación estructurada (fuente de verdad)
    document.md      ← formato maestro exportable
    document.docx
    document.pdf
    document.txt
```

Cada documento va en su propia carpeta para que distintos tipos/idiomas del mismo vídeo no se sobrescriban. Los nombres de archivo de descarga se sanean (nunca se usa el título de YouTube tal cual como ruta).

### API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/health` | Estado, proveedor, modelo configurado y proxy de transcripción (`transcript_proxy`) |
| POST | `/api/videos/inspect` | `{url}` → vídeo, metadatos, transcripción disponible/idioma/tipo. No genera nada |
| POST | `/api/documents` | `{url, document_type, output_language, force_regenerate}` → 202 + id; procesa en segundo plano |
| GET | `/api/documents` | Documentos recientes (historial) |
| GET | `/api/documents/{id}` | Estado, progreso y, al terminar, Markdown + documento estructurado |
| GET | `/api/documents/{id}/download/{markdown\|docx\|pdf\|txt}` | Descargas |
| GET | `/api/videos/{video_id}/transcript` | Transcripción original con timestamps |
| POST | `/api/videos/inspect-batch` | `{urls: [...]}` → inspección independiente de cada vídeo, líneas no válidas y duplicadas |
| POST | `/api/document-collections` | `{urls, document_type, output_language, output_mode, force_regenerate}` → 202 + id |
| GET | `/api/document-collections` | Colecciones recientes (historial) |
| GET | `/api/document-collections/{id}` | Estado, progreso, vídeos (con su documento individual) y, al terminar, el consolidado |
| GET | `/api/document-collections/{id}/download/{markdown\|docx\|pdf\|txt}` | Descargas del consolidado |

Errores: `{"error": {"code", "message", "detail"}}` con códigos estables (`INVALID_YOUTUBE_URL`, `VIDEO_NOT_FOUND`, `TRANSCRIPT_NOT_AVAILABLE`, `TRANSCRIPT_DISABLED`, `TRANSCRIPT_FETCH_FAILED`, `AI_NOT_CONFIGURED`, `AI_PROCESSING_FAILED`, `DOCUMENT_GENERATION_FAILED`…). El frontend los convierte en mensajes legibles; nunca se muestran trazas.

## 8. Tipos de documento y formatos

| Modo | Descripción |
|---|---|
| `full_notes` *(por defecto)* | Apuntes completos: todas las ideas relevantes reorganizadas, sin empobrecer |
| `summary` | Idea central, puntos importantes, conceptos y conclusiones |
| `step_by_step` | Procedimientos convertidos en pasos numerados |
| `study_guide` | Conceptos, definiciones, reglas, errores, checklist y preguntas de repaso |
| `trading` | Setup, confirmación, entrada, stop, gestión… solo lo que aparece en el vídeo; señala ausencias relevantes |
| `clean_transcript` | La transcripción con puntuación, párrafos y títulos, modificando lo mínimo |

Formatos (todos generados desde la representación estructurada): **Markdown** (maestro; timestamps como enlaces `[18:42 – 21:10 ↗](…&t=1122s)`), **DOCX** (python-docx; cabecera con título, canal, URL, idioma, tipo y fecha; timestamps como hipervínculos), **PDF** (ReportLab, sin Word; tipografía jerárquica, tablas, citas, enlaces), **TXT** (documento procesado en texto plano; no la transcripción, salvo en `clean_transcript`).

## 9. Tests

```bash
cd backend
pytest                      # 193 tests unitarios y de API: sin red, sin OpenAI, sin YouTube
ruff check . && ruff format --check .
mypy app

cd ../frontend
npm run typecheck && npm run lint && npm run build
```

Los tests simulan `TranscriptProvider` (fixture) y `AIProcessor` (stub), y ejercitan el pipeline con un LLM guionizado. Cubren: extracción de IDs y URLs inválidas, normalizador, timestamps, chunking, selección de idioma, Markdown/DOCX/PDF/TXT, saneado de nombres, fingerprint de caché, validez de los esquemas en modo estricto, parseo de Structured Outputs, errores del proveedor y de OpenAI, y endpoints.

**Tests de integración opcionales** (llaman a servicios reales; desactivados por defecto):

```bash
RUN_INTEGRATION=1 pytest tests/integration                  # YouTube + oEmbed
RUN_INTEGRATION=1 OPENAI_API_KEY=... pytest tests/integration   # + una llamada pequeña a OpenAI (tiene coste)
# INTEGRATION_VIDEO_ID=<id> para elegir el vídeo
```

## 10. Limitaciones

- **La aplicación depende de que exista una transcripción de YouTube accesible. Si no existe, el vídeo no se procesa.**
- **El proveedor inicial de transcripciones utiliza `youtube-transcript-api`; cambios internos en YouTube podrían requerir actualizar o sustituir este proveedor.** Todo el acoplamiento está en `services/transcripts/youtube.py`, detrás de la interfaz `TranscriptProvider`.
- YouTube puede bloquear temporalmente peticiones desde algunas IPs (sobre todo de proveedores cloud); se informa como `TRANSCRIPT_FETCH_FAILED`. En esos entornos se puede activar el [proxy residencial opcional](#proxy-para-youtube-opcional).
- Las transcripciones automáticas contienen errores de reconocimiento (p. ej. "mis top" en vez de "mi stop"). Los prompts y la verificación contra la transcripción original los tienen en cuenta, pero la calidad del documento está limitada por la de la transcripción.
- La fidelidad se refuerza con prompts, verificación y salvaguardas deterministas, pero un LLM puede equivocarse: los timestamps permiten comprobar cada sección en el vídeo.
- La verificación revisa cada capítulo por separado; puede quedar alguna repetición menor entre capítulos.
- Multi-URL: no se admiten playlists ni canales completos. La duración de un vídeo solo se muestra al analizar si su transcripción ya está guardada localmente. La etapa de consolidación recibe todas las ideas extraídas de todos los vídeos en una sola llamada: con lotes muy grandes de vídeos largos puede ser costosa.
- Los trabajos se ejecutan en el proceso del backend: si se reinicia a mitad de un documento, este queda marcado como fallido (hay que volver a generarlo).
- Sin usuarios ni autenticación: pensada para ejecución local.
- El nombre del idioma de la transcripción lo devuelve YouTube en su propio idioma de interfaz (p. ej. "Spanish").
