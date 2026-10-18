# Buscador híbrido — especificación v1

El buscador que necesita el agente de Continuidad, decidido por lo que
midió su banco (`docs/agente_continuidad.md`, «Qué le pide al buscador»).
No sustituye a `recuperar.py` (el buscador del lector, con filtro
anti-spoiler): son vías nuevas de `continuidad.reunir_contexto`.

## Punto de partida (banco, 2026-10-18)

| Medida | v1, solo la vía de entidades |
|---|---|
| Cobertura del contexto | 15/16: falla **S11** («Y blandía en la diestra la espada…»: el sujeto, Menelao, está en el párrafo anterior) |
| Tamaño del contexto | Hasta 101-110 hechos: los colectivos («aqueos», «troyanos») traen decenas de hechos irrelevantes |
| Agente con API | 14/16 detectados, 0 avisos falsos |

## Vías

1. **entidades** (ya existe): entidades nombradas en el pasaje.
2. **entorno** (nueva): entidades nombradas en el **párrafo anterior** al
   punto de inserción (párrafo = bloque de texto separado por línea en
   blanco; si no hay, los 1500 caracteres anteriores). Solo las que no
   estén ya en el pasaje. Origen: `entorno: <término>`. Mismo criterio de
   detección y de hechos que la vía de entidades.
3. **lexica** (nueva): búsqueda de texto completo de Postgres sobre
   `hechos_canonicos.hecho`, configuración `spanish`. La consulta son las
   palabras con contenido del pasaje (sin vacías, las que descarta la
   propia configuración `spanish`) unidas con OR; se ordena por
   `ts_rank_cd` y se toman las 30 primeras. Origen: `lexica`. Índice GIN
   sobre la expresión `to_tsvector('spanish', hecho)` en `schema.sql` (solo
   un índice: no cambia ninguna tabla).

Cada vía devuelve, por origen, una lista **ordenada**:

- entidades y entorno: los hechos de la entidad, del más cercano al punto
  de inserción al más lejano (distancia en `posicion_global`, tomando como
  punto el fragmento de inserción);
- lexica: por `ts_rank_cd`, de mayor a menor.

## Fusión y tope

**Fusión por rango recíproco (RRF)**: la puntuación de un hecho es la suma,
sobre todas las listas en que aparece, de `1 / (60 + puesto)`. Se ordena
por puntuación y entran los `tope` primeros; después se presentan en orden
de lectura, como ahora. Un hecho que aparece en el puesto 80 de la lista
de «aqueos» puntúa poco; uno que aparece en tres listas cortas, mucho.

`tope` por defecto pasa de 200 a **60** solo si la medida lo justifica (ver
abajo). Las vías y el tope siguen siendo parámetros de `reunir_contexto`;
cada hecho sigue listando todas las vías que lo trajeron.

## Cómo se mide (sin API)

Ampliación de la prueba de cobertura (`tests/continuidad/test_contexto_db.py`,
solo `--db`): para cada combinación de vías y tope,

| Combinación | Vías | Tope |
|---|---|---|
| actual | entidades | 200 |
| +entorno | entidades, entorno | 200 |
| +lexica | entidades, lexica | 200 |
| híbrido | entidades, entorno, lexica | 200 |
| híbrido-60 | entidades, entorno, lexica | 60 |

se informa, por caso: si llega algún hecho esperado, **en qué puesto** del
ranking fusionado queda el mejor de ellos, y cuántos hechos tiene el
contexto. También el tamaño del contexto en controles y trampas. El
informe sale como tabla en `.cache/continuidad/buscador.txt`.

**Criterio para cambiar el valor por defecto** a híbrido-60: cobertura
16/16, ningún hecho esperado por debajo del puesto 60, y contexto medio
menor que el actual.

## Comprobación final con la API (mínima)

El banco con API (`pytest --db --api tests/continuidad`) solo se repite
cuando cambie el valor por defecto, y **solo para los casos cuyo contexto
haya cambiado**: con `NOVELA_BANCO_SOLO_CAMBIADOS=1`, un caso cuyo
conjunto de hechos en el contexto coincide con el de su respuesta grabada
(`tests/fijos/continuidad/respuestas/`) reutiliza esa respuesta en vez de
llamar al modelo, y el informe dice cuántas llamadas se hicieron. El
umbral del agente no cambia (≥ 13/16, ≤ 1 aviso en controles y trampas).

## Pruebas

| Spec | Prueba |
|---|---|
| El párrafo anterior al punto de inserción se extrae bien (con y sin líneas en blanco, al principio del fragmento) | R |
| La vía de entorno no repite entidades que ya nombra el pasaje | R |
| La consulta léxica se construye con las palabras del pasaje unidas con OR; un pasaje sin palabras con contenido no consulta nada | R |
| RRF: un hecho en varias listas puntúa más; el tope se aplica después de fusionar; el resultado sale en orden de lectura | R |
| Las listas de entidades se ordenan por cercanía al punto de inserción | R |
| La vía léxica encuentra el hecho de la espada rota (3.45) para S11 | D |
| La vía de entorno detecta a Menelao para S11 | D |
| Informe del buscador: cobertura, puesto y tamaño por caso y combinación | D |
| Con `NOVELA_BANCO_SOLO_CAMBIADOS=1`, un caso con el mismo contexto no llama a la API | R (con el cliente falso) |

## Fuera de alcance

- Semántica sobre hechos (embeddings por hecho): se decidirá si la
  cobertura híbrida no llega a 16/16.
- Posición en caracteres de cada hecho (dirección dentro del mismo
  fragmento, reescritura de pasajes): cambio de `extraer_canon.py`, aparte.
- El buscador del lector (`recuperar.py`): no se toca, salvo para
  compartir funciones.
