# Buscador del lector — especificación v1 (borrador)

> **Borrador para revisar.** El banco (`docs/buscador_lector_casos.json`)
> tampoco está fijado: se fija, y se mueve a `tests/fijos/lector/casos.json`,
> cuando esta especificación se dé por buena y antes de programar.

El buscador que usarán los agentes que evalúan **desde un punto de
lectura** (Estructura y ritmo, Crítica de conjunto): lo que el lector ya
sabe en ese punto, y nada posterior. Es la pieza que el diagrama marca «en
parte»: `recuperar.py` ya busca pasajes y hechos con filtro anti-spoiler,
pero por separado y sin ordenar los hechos por relevancia.

## El consumidor todavía no existe

El buscador de Continuidad lo decidió su consumidor: el banco del agente
midió qué hechos le llegaban y cuáles no. Aquí los consumidores no existen
todavía. Para no diseñar en abstracto, el banco hace de consumidor: son
**preguntas que se haría un lector editorial en un punto concreto de la
obra**, con los hechos que deberían llegar y los que no pueden llegar
porque van después.

Cuando se especifiquen Estructura y ritmo o Crítica de conjunto, sus
bancos sustituyen o amplían a este, y el buscador se vuelve a medir.

## Decisiones v1

| Tema | Decisión v1 |
|---|---|
| Entrada | Una consulta (texto libre: una pregunta o un pasaje) y un **punto de lectura**: `hasta_fragmento` y, opcional, `hasta_char` |
| Salida | Una sola respuesta con dos listas: **hechos** (fusionados por rango recíproco, como en Continuidad) y **pasajes** (búsqueda semántica) |
| Garantía | **Nada posterior al punto de lectura**, ni en hechos ni en pasajes. El filtro va en el `WHERE` de cada consulta, nunca como filtro posterior |
| Vías de hechos | Las de Continuidad: entidades y léxica por defecto (las que ganaron en su banco); entorno disponible |
| Orden de cada vía | Del hecho más cercano al punto de lectura hacia atrás |
| Tope | 60 hechos, como en Continuidad; 5 pasajes |
| Lectura parcial | La regla de hoy: el fragmento en curso no aporta hechos (los hechos no tienen posición en caracteres); sí aporta los pasajes que terminan antes de `hasta_char` |
| Código | Las vías y la fusión pasan de `continuidad.py` a un módulo común, `src/buscador.py`, con un parámetro `limite` (ninguno para Continuidad, el punto de lectura para el lector). Ver dudas |

## Interfaz

```python
from recuperar import consultar

respuesta = consultar(cur, obra, consulta, hasta_fragmento, hasta_char=None,
                      modelo_embeddings=None, vias=("entidades", "lexica"),
                      tope=60, k_pasajes=5)
respuesta.hechos       # lista en orden de lectura, cada uno con origen y puesto
respuesta.pasajes      # los más parecidos, nunca posteriores
respuesta.omitidos     # hechos que no entraron por el tope
```

`buscar_pasajes` y `hechos_hasta` siguen como están: los usan las pruebas
anti-spoiler y la línea de comandos.

## Cómo se mide

Banco en borrador, `docs/buscador_lector_casos.json`: 16 preguntas sobre
los Cantos I-IV.

| Categoría | Casos | Qué se espera |
|---|---|---|
| Memoria | 6 | La respuesta está en un canto ya leído: llega algún hecho esperado |
| Parcial | 2 | Punto a mitad de canto. P01: la respuesta está en un canto anterior y debe llegar. P02: está en el canto en curso, antes del punto; con la regla de hoy no puede llegar como hecho, y el caso mide cuánto cuesta esa regla |
| Spoiler | 4 | El desenlace va después del punto: **ningún hecho de `fugas` puede llegar** |
| Control | 4 | La misma consulta que cada spoiler, desde un punto posterior: el hecho que antes era fuga sí llega. Sin el control, un spoiler que no llega podría no probar nada |

Cada caso con `esperados` cuenta como cubierto si algún hecho esperado
está en la respuesta (`<orden>.<índice>` → `posicion_global`).

**Umbral de aceptación:**

- **fugas: ninguna**, en ningún caso. Es la garantía del buscador; se
  comprueba con base de datos;
- cobertura de memoria y controles: **10 de 10**;
- los parciales se informan aparte, sin umbral: P02 es la medida de lo que
  costaría no guardar la posición en caracteres de los hechos.

### Medida previa (2026-11-01)

Sin base de datos, con los JSON del canon y del glosario tras el Canto IV,
**solo la vía de entidades** con la regla de lectura parcial (la léxica
necesita Postgres):

| | Resultado |
|---|---|
| Memoria | 6/6 |
| Parciales | P01 1/1; P02 0/2 (la respuesta está en el canto en curso) |
| Controles | 3/4: E02c no nombra a nadie («¿Quién rompió los juramentos de la tregua?») y solo puede llegar por la vía léxica |
| Fugas | 0 en los cuatro spoilers. Sin el límite, llegarían 5 de 5 en E01, 4 de 4 en E03 y 1 de 1 en E04: los spoilers son relevantes para la consulta, no casualidades |
| Tamaño | De 0 a 107 hechos antes del tope; M02 trae 107 por el colectivo «aqueos» |

## Pruebas (a añadir a tests/SPECS.md)

| Spec | Prueba |
|---|---|
| Con un límite, cada vía filtra en el `WHERE` (la consulta lleva la condición de posición) | R (cursor falso) |
| Sin límite, las vías dan lo mismo que hoy en Continuidad | R, D |
| Las listas se ordenan por cercanía al punto de lectura | R |
| Ningún hecho ni pasaje del banco es posterior a su punto de lectura | D |
| Cada spoiler tiene un control que sí encuentra alguna fuga | D |
| Cobertura del banco por caso y vía, con el tamaño de la respuesta (informe en `.cache/lector/buscador.txt`) | D |
| Construcción de los casos (punto por verso) y referencias a hechos que existen | R |

Todo se mide sin API: el buscador no llama al modelo.

## Fuera de alcance

- **Posición en caracteres de cada hecho**: es la decisión pendiente sobre
  `extraer_canon.py`. P02 mide lo que se pierde sin ella.
- **Lo que cada agente necesite de verdad**: este banco es provisional.
- **Semántica sobre hechos** (embeddings por hecho): solo si la cobertura
  con entidades y léxica no llega al umbral.

## Dudas para decidir antes de fijar

1. **Módulo común o duplicar.** Llevar las vías y la fusión de
   `continuidad.py` a `src/buscador.py` evita mantener dos copias, pero toca
   Continuidad, que ya funciona. La alternativa es que `recuperar.py`
   importe las vías de `continuidad.py` y les pase el límite, sin mover nada.
2. **Orden por cercanía.** Para el lector lo más reciente suele pesar más,
   pero en M02 («¿Por qué Aquiles no combate?») la respuesta está en el
   Canto I, lo más lejano. La fusión lo compensa si llega por dos vías.
   ¿Cercanía o solo relevancia?
3. **Umbral 10 de 10** en memoria y controles: el banco es pequeño y cada
   caso pesa mucho. ¿Exigir todos o dejar margen de 1?
4. **¿Esperar a los agentes?** Se puede fijar ya este banco provisional, o
   especificar primero Estructura y ritmo y que sea su banco el que mida
   el buscador, como en Continuidad.
