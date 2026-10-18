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
| Banco de Continuidad | `pytest --db --api tests/continuidad` (desde WSL, con `WSLENV=ANTHROPIC_API_KEY`) | minutos | Postgres + pgvector y `ANTHROPIC_API_KEY`; 27 llamadas, con coste |

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
| Con las 3 pasadas grabadas de la Ilíada, el resultado es el mismo que el 2026-10-03, salvo la corrección de los dos Áyax y las formas compartidas | S |

**Variantes y epítetos.** El modelo separa `variantes` (formas que nombran a
esa entidad y solo a ella) de `epitetos` (fórmulas descriptivas que podrían
aplicarse a otras). Los epítetos se guardan, pero no intervienen en la
fusión, en la identificación de entidades fijadas ni en la búsqueda de hechos.

| Spec | Prueba |
|---|---|
| Una respuesta sin el campo `epitetos` vale como `[]` (siguen valiendo las respuestas grabadas) | R, S |
| Los epítetos de todas las pasadas se unen; si una pasada da una forma como variante y otra como epíteto (aunque cambien las mayúsculas), gana epíteto; un epíteto nunca acaba como alias | R |
| Dos entidades que comparten un epíteto no se fusionan ni lo tienen como alias | R |
| Una calibración real separa al menos un epíteto conocido del Canto I, sin repetirlo como variante | A |

**Formas compartidas** (red de seguridad determinista, por si el modelo se equivoca):

| Spec | Prueba |
|---|---|
| Una forma asociada a dos o más entidades, en cualquier registro, sale de los alias y variantes de todas y va a discrepancias como «forma compartida» («domador de caballos» en Héctor y Cástor; «Atrida» en Agamenón y Menelao; «el Olímpico» en Júpiter y Olimpo) | R, S |
| El término canónico de una entidad nunca se le retira | R |
| Una forma ya declarada compartida no vuelve a asignarse en calibraciones posteriores | R |
| `--sanear` aplica la regla a un glosario fijado sin llamar a la API, sin cambiar términos, y es idempotente | R |
| `--sanear` lista como candidatos a epíteto los alias en minúscula sin nombre propio, sin mover nada | R |
| `--epitetos=<lista>` mueve a `epitetos` solo las formas confirmadas a mano | R |

**Calibración incremental** (por defecto si la obra ya tiene glosario; `--desde-cero` para recalibrar todo):

| Spec | Prueba |
|---|---|
| Solo se envían los fragmentos nuevos, con el glosario fijado en el prompt | S |
| Ningún término fijado desaparece ni cambia de forma canónica, registro o categoría | R, S |
| El canon ya extraído sigue usando términos válidos: no hay que reextraer | R |
| Un término nuevo va a su registro (personajes, lugares o glosario) | R, S |
| Una forma nueva de un término fijado se le añade como variante y alias | R, S |
| Un nombre que ya es variante de un término fijado no se duplica como término | R |
| Compartir solo un epíteto con una entidad fijada no la identifica (caso Climene / Juno): el epíteto pasa a forma compartida y no se queda en ninguna | R |
| Una entidad nueva que trae un epíteto ya usado no se fusiona con la existente | R |
| Una forma que ya pertenece a otra entidad no se reasigna: si la reclaman dos, va a discrepancias como forma compartida (no gana la primera) | R |
| Los epítetos nuevos de un término fijado se le añaden, sin identificarlo, y no vuelven como variante con otras mayúsculas | R |
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
| Los hechos de una entidad no incluyen los que solo coinciden por un epíteto («domador de caballos» de Héctor; «valiente en la pelea» de Diomedes no sale como hecho de Menelao) | D |
| Un epíteto no sirve para identificar a una entidad en la búsqueda | D |
| Cada consulta de referencia de la Ilíada encuentra su canto, con la palabra clave entre los 5 primeros | D |

## Agente de Continuidad — continuidad.py

Especificación completa en [`docs/agente_continuidad.md`](../docs/agente_continuidad.md).
Entrada: un pasaje nuevo y su posición (fragmento y carácter). Salida: un
`Informe` con avisos (solo apoyados en hechos del narrador), notas,
descartados por la validación y hechos omitidos por el tope.

| Spec | Prueba | Dónde |
|---|---|---|
| Las entidades del pasaje se detectan por término, alias o variante, como palabra completa y sin distinguir mayúsculas | R | [test_continuidad.py](test_continuidad.py) |
| Un epíteto o una forma compartida no trae entidades | R, D | [test_continuidad.py](test_continuidad.py), [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| El contexto incluye hechos posteriores al punto de inserción, con su dirección; entre fragmentos, la dirección es correcta | D | [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| Los hechos de cada entidad siguen el mismo criterio que `recuperar.hechos_hasta(entidad=...)`, sin límite de posición | D | [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| El prompt lleva cada hecho con su identificador, su atribución y su dirección | R | [test_continuidad.py](test_continuidad.py) |
| Un aviso con cita no literal o con un hecho fuera del contexto se descarta | R | [test_continuidad.py](test_continuidad.py) |
| Un aviso apoyado en un hecho de personaje pasa a notas | R | [test_continuidad.py](test_continuidad.py) |
| Entre fragmentos, la dirección la fija el código | R | [test_continuidad.py](test_continuidad.py) |
| Valores de `tipo`, `gravedad` o `direccion` no permitidos descartan el aviso, y todo lo descartado se cuenta | R | [test_continuidad.py](test_continuidad.py) |
| Con más hechos que el tope, se omiten los peor situados en la fusión (los del fondo de las listas largas, como las de los colectivos) y se informa | R, D | [test_continuidad.py](test_continuidad.py), [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| La consulta es una sola llamada, con el modelo del resto de fases, y acepta el JSON envuelto en ```` ```json ```` | S | [test_continuidad.py](test_continuidad.py) |
| Cada hecho del contexto indica por qué vía llegó | R, D | [test_continuidad.py](test_continuidad.py), [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| Cada caso del banco se construye sobre el texto de su fragmento, y sus hechos esperados existen y son del narrador | R | [continuidad/test_banco_casos.py](continuidad/test_banco_casos.py) |
| Con respuestas grabadas del banco, el informe se reproduce igual | S | [continuidad/test_banco_casos.py](continuidad/test_banco_casos.py) |
| Cobertura del contexto en el banco (informativa, sin umbral en v1), con el motivo de cada hecho que no llega | D | [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| Banco: ≥ 13/16 detectados, ≤ 1 aviso en controles y trampas; cada fallo clasificado como del buscador o del agente | A + D | [continuidad/test_banco_api.py](continuidad/test_banco_api.py) (puntuación y clasificación también en R: [continuidad/test_banco_casos.py](continuidad/test_banco_casos.py)) |
| El 100 % de los avisos que llegan al informe cumplen las garantías de *Validar* | A + D | [continuidad/test_banco_api.py](continuidad/test_banco_api.py) |

El banco escribe su informe (`banco.json`, `banco.txt`, `cobertura.json`)
y las respuestas en bruto de cada caso (`respuestas/<caso>.json`) en
`.cache/continuidad/` (o en `NOVELA_SALIDA_CONTINUIDAD`). Para que la
suite rápida las reproduzca, se copian a mano a
`tests/fijos/continuidad/respuestas/` tras revisarlas.

### Buscador híbrido

Especificación en [`docs/buscador_hibrido.md`](../docs/buscador_hibrido.md).
Vías nuevas de `reunir_contexto`: `entorno` (entidades del párrafo anterior
al punto de inserción que el pasaje no nombra) y `lexica` (texto completo
de Postgres sobre los hechos). Las listas de cada vía se fusionan por
rango recíproco y el tope se aplica después. Por defecto siguen solo la
vía de entidades y el tope de 200.

| Spec | Prueba | Dónde |
|---|---|---|
| El párrafo anterior al punto de inserción se extrae bien (con y sin líneas en blanco, al principio del fragmento) | R | [test_continuidad.py](test_continuidad.py) |
| La vía de entorno no repite entidades que ya nombra el pasaje | R, D | [test_continuidad.py](test_continuidad.py), [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| La consulta léxica se construye con las palabras del pasaje unidas con OR; un pasaje sin palabras con contenido no consulta nada | R, D | [test_continuidad.py](test_continuidad.py), [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| El índice GIN de `schema.sql` usa la misma expresión que la consulta léxica | R | [test_continuidad.py](test_continuidad.py) |
| RRF: un hecho en varias listas puntúa más; el tope se aplica después de fusionar; el resultado sale en orden de lectura | R | [test_continuidad.py](test_continuidad.py) |
| Las listas de entidades se ordenan por cercanía al punto de inserción | R | [test_continuidad.py](test_continuidad.py) |
| La vía léxica encuentra el hecho de la espada rota (3.45) para S11 | D | [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| La vía de entorno detecta a Menelao para S11 | D | [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| Informe del buscador: cobertura, puesto y tamaño por caso y combinación (`.cache/continuidad/buscador.txt`), con el criterio para pasar a híbrido-60 | R (tabla), D | [continuidad/test_banco_casos.py](continuidad/test_banco_casos.py), [continuidad/test_contexto_db.py](continuidad/test_contexto_db.py) |
| Con `NOVELA_BANCO_SOLO_CAMBIADOS=1`, un caso con el mismo contexto no llama a la API | R (con el cliente falso) | [continuidad/test_banco_casos.py](continuidad/test_banco_casos.py) |

