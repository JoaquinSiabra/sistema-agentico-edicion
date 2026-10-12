# Specs del sistema

Qué debe cumplir cada fase, y qué prueba lo comprueba. Las fases que llaman
a Claude no dan siempre la misma salida, así que sus specs son
**propiedades** que toda respuesta válida cumple, no salidas exactas.

## Cómo se ejecutan

| Nivel | Comando | Tiempo | Qué necesita |
|---|---|---|---|
| Rápido | `uv run pytest` | segundos | nada |
| Base de datos | `pytest --db` (desde WSL) | ~20 s (4-5 min la 1ª vez) | Postgres + pgvector |
| API real | `uv run pytest --api tests/api` | ~1-2 min | `ANTHROPIC_API_KEY`; 3 llamadas, con coste |

Leyenda de la columna «Prueba»: **R** rápido · **D** base de datos ·
**A** API real · **S** API simulada con respuestas grabadas (también rápido) ·
**pendiente** = spec acordada, aún no implementada.

## Fase 0a — calibrar_taxonomia.py

Entrada: fragmentos de la obra. Salida: `data/<obra>/exports/taxonomia_pasada_N.json`
y, con una sola pasada, `tipos_documentales_obra.json`.

| Spec | Prueba |
|---|---|
| Cada tipo tiene nombre y descripción; no hay nombres repetidos | A |
| Con `--n=1`, la pasada se copia tal cual como taxonomía de la obra | S |
| Con varias pasadas no se elige ninguna: se dejan para comparar a mano | S |

## Fase 0b — calibrar_glosario.py

Entrada: fragmentos de la obra. Salida: las N pasadas en bruto,
`personajes_obra.json`, `lugares_obra.json`, `glosario_obra.json` y
`discrepancias_calibracion.json`.

| Spec | Prueba |
|---|---|
| Los fragmentos se envían numerados, en orden y con caché | S |
| Se acepta la respuesta aunque venga envuelta en ```` ```json ```` | S |
| Una pasada con JSON roto se descarta sin perder las demás; si fallan todas, se aborta | S |
| Toda entrada tiene categoría permitida, confianza, evidencia, y su término no se repite como variante | A |
| Ningún término es inventado: el término o alguna variante aparece en el texto | A |
| Los protagonistas obvios aparecen como persona | A |
| La categoría la decide la mayoría de pasadas; el desacuerdo queda registrado | R |
| Un empate sin mayoría va a discrepancias, no al glosario | R |
| Entre pasadas que coinciden, se conserva la entrada de mayor confianza | R |
| Las variantes de todas las pasadas se unen | R |
| Fusión de alias: por variante reportada siempre; por subcadena (palabra completa) solo en personajes | R |
| Dos entidades distintas nunca acaban una como alias de la otra (caso Áyax Telamonio / Áyax de Oileo) | R |
| Un término cuyas variantes son varias entidades distintas (un «Ayax» genérico) no entra en el glosario: va a discrepancias | R |
| Con las 3 pasadas grabadas de la Ilíada, el resultado es el mismo que el 2026-10-03, salvo la corrección de los dos Áyax | S |

**Calibración incremental** (por defecto si la obra ya tiene glosario; `--desde-cero` para recalibrar todo):

| Spec | Prueba |
|---|---|
| Solo se envían los fragmentos nuevos, con el glosario fijado en el prompt | S |
| Ningún término fijado desaparece ni cambia de forma canónica, registro o categoría | R, S |
| El canon ya extraído sigue usando términos válidos: no hay que reextraer | R |
| Un término nuevo va a su registro (personajes, lugares o glosario) | R, S |
| Una forma nueva de un término fijado se le añade como variante y alias | R, S |
| Un nombre que ya es variante de un término fijado no se duplica como término | R |
| Compartir solo un epíteto con una entidad fijada no la identifica (caso Climene / Juno), ni el epíteto ajeno pasa al término nuevo | R |
| Una forma que ya pertenece a otra entidad no se reasigna | R |
| Una categoría distinta de la fijada, o un nombre de varias entidades, va a discrepancias | R |
| Las pasadas en bruto y las discrepancias anteriores no se pierden | S |

## Fase 1 — extraer_canon.py

Entrada: un fragmento, su `--orden` y la calibración de la obra. Salida:
`data/<obra>/exports/canon/NNNN.json`.

| Spec | Prueba |
|---|---|
| Exige `--obra`, `--orden` y el fragmento antes de gastar ninguna llamada | S |
| El prompt de sistema lleva todos los tipos documentales y todos los términos calibrados, y va con caché | S |
| Sin calibración, el prompt es solo la base | S |
| La salida añade `orden_fragmento` y la ruta absoluta del texto | S |
| Si el JSON llega roto, la respuesta cruda se guarda para depurar | S |
| Las categorías de atribución del prompt son las mismas que acepta `schema.sql` | R |
| Cada hecho tiene texto, momento y atribución válida; `narrador_documental` ⇔ sin personaje | A, y R sobre lo grabado |
| `tipo_documental` es uno de los tipos calibrados | A |
| Personajes, lugares y quien afirma cada hecho se nombran con la forma canónica, no con una variante | A, y R sobre lo grabado |

## Fase 2 — poblar_almacen.py

| Spec | Prueba |
|---|---|
| Los pasajes no superan el límite del modelo de embeddings y no dejan huecos de texto | R (tokenizador falso), D (tokenizador real) |
| Cada pasaje cita exactamente su trozo del fragmento (`inicio_char`/`fin_char`) | D |
| La ruta del texto guardada desde Windows se encuentra desde WSL, y al revés | R |
| Todos los hechos se cargan, en orden, con `posicion_global = orden * 10000 + índice` | D |
| El glosario entero se carga como entidades; cada atribución queda enlazada a su personaje | D |
| Recargar con un glosario ampliado actualiza los alias de las entidades existentes y añade las nuevas | D |
| Recargar lo mismo no cambia nada | D |
| Una carga que falla a medias no deja nada | D |

## Recuperación — recuperar.py

| Spec | Prueba |
|---|---|
| Nunca devuelve un pasaje posterior al punto de lectura (fragmento y carácter) | D |
| Nunca devuelve un hecho posterior; con lectura parcial, el fragmento en curso no aporta hechos | D |
| El filtro no hace perder resultados permitidos (el índice HNSW los encuentra todos) | D |
| Una entidad se encuentra por su término, alias o variante | D |
| Cada consulta de referencia de la Ilíada encuentra su canto, con la palabra clave entre los 5 primeros | D |
