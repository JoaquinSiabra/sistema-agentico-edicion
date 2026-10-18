# Agente de Continuidad — especificación v1

Herramienta del **autor**: dado un pasaje que se va a insertar en la obra,
avisa de lo que contradice el canon ya establecido. No corrige estilo ni
opina sobre la trama.

## Decisiones de diseño

| Tema | Decisión v1 |
|---|---|
| Qué evalúa | **Hechos y estado**: contradicciones con hechos canónicos, incluido el estado de personajes y objetos (vivo o muerto, herido, dónde está, qué tiene, qué sabe). |
| Fuera de alcance | Cronología fina, caracterización («X no hablaría así»), reescritura de un pasaje existente (ver *Límites*). |
| Horizonte | **Todo el canon**, anterior y posterior al punto de inserción: al autor le importa también lo que viene después. No se aplica el filtro anti-spoiler del lector. |
| Entrada | Un **pasaje nuevo** (de unos cientos a un par de miles de palabras) y su **posición**: fragmento (`orden`) y carácter de inserción. |
| Atribución | Solo puede fundar un aviso un hecho del **narrador** (`narrador_documental`). Lo que afirma un personaje puede ser falso: si el pasaje lo contradice, o un personaje contradice al narrador, como mucho es una **nota**, nunca un aviso. |
| Gravedad | Dos niveles: **contradicción** (imposible tal como está) y **tensión** (posible, pero necesita explicación). |
| Umbral | Ver *Cómo se mide*. |

## Flujo

```
pasaje + posición
   │
   ├─ 1. reunir_contexto (determinista, base de datos)
   │      entidades del pasaje → sus hechos en todo el canon (+ dirección)
   │      + pasajes del almacén más parecidos
   │
   ├─ 2. consultar_modelo (una llamada a Claude)
   │      → avisos y notas en JSON
   │
   └─ 3. validar (determinista, sin base de datos ni API)
          descarta o corrige lo que no se sostiene → Informe
```

### 1. Reunir el contexto

1. **Entidades del pasaje**: toda entidad de la obra cuyo término, alias o
   variante aparezca en el pasaje como palabra completa, sin distinguir
   mayúsculas. **Nunca por epítetos** (campo `epitetos` del glosario, que
   no está en la base de datos) ni por formas compartidas (ya retiradas de
   los alias por `calibrar_glosario.py`): «domador de caballos» no debe
   traer los hechos de Cástor.
2. **Hechos** de esas entidades en **todo** el canon, con el mismo criterio
   de coincidencia que `recuperar.hechos_hasta(entidad=...)` pero sin el
   límite de posición. Cada hecho lleva su `posicion_global`, su texto, su
   tipo de atribución y quién lo afirma.
3. **Dirección** de cada hecho respecto al punto de inserción:
   - fragmento anterior → `anterior`; posterior → `posterior`;
   - mismo fragmento → `mismo_fragmento`. Los hechos no guardan posición en
     caracteres, así que aquí no se puede decidir sin modelo: lo decide él,
     con ayuda del texto que rodea al punto de inserción (≈1500 caracteres
     antes y después).
4. **Pasajes parecidos**: los 5 más cercanos por embedding
   (`recuperar.buscar_pasajes`, sin límite de posición). Solo son contexto:
   un aviso nunca puede citar un pasaje, solo un hecho.
5. Tope de tamaño: si hay más de 200 hechos, se quedan los de las
   entidades con menos hechos primero (las más específicas) hasta llenar el
   tope, y el informe dice cuántos se omitieron.
6. **Origen**: cada hecho del contexto lleva qué vía lo trajo (`entidad:
   <término>`, y en el futuro `lexico`, `semantico`…). Un hecho traído
   por varias vías las lista todas. Es lo que permite medir el buscador
   (ver *Qué le pide al buscador*).

Las vías de recuperación son **intercambiables**: `reunir_contexto` recibe
la lista de vías a usar (en v1, solo `entidades`), para poder comparar
combinaciones sobre el mismo banco sin tocar el resto del agente.

### 2. Consultar al modelo

Una llamada, mismo modelo que el resto de fases (`MODELO` en
`calibrar_glosario.py`). El prompt de sistema explica las reglas:

- un aviso solo puede apoyarse en un hecho marcado como del narrador;
- lo que dice un personaje (en el canon o en el pasaje) puede ser falso o
  interesado: si choca con algo, va a `notas`;
- definiciones de contradicción y tensión, con un ejemplo de cada una;
- la cita del pasaje debe ser **literal**;
- si no hay nada, listas vacías: no hay que encontrar algo a la fuerza.

Cada hecho se presenta con un identificador (`posicion_global`), su
atribución y su dirección. Respuesta, solo JSON:

```json
{
  "avisos": [
    {
      "cita": "fragmento literal del pasaje",
      "hecho": 40021,
      "tipo": "hecho | estado",
      "gravedad": "contradiccion | tension",
      "direccion": "anterior | posterior",
      "explicacion": "una o dos frases"
    }
  ],
  "notas": [
    {"cita": "…", "hecho": 30029, "explicacion": "…"}
  ]
}
```

### 3. Validar (garantías deterministas)

El modelo se equivocará a veces; estas reglas no dependen de él:

1. Un aviso cuya `cita` no aparezca literalmente en el pasaje (comparando
   con espacios normalizados) **se descarta**.
2. Un aviso cuyo `hecho` no esté entre los del contexto **se descarta**.
3. Un aviso apoyado en un hecho que **no** es del narrador **pasa a
   notas**.
4. Si el hecho es de otro fragmento, la `direccion` la fija el código
   (la del contexto), diga lo que diga el modelo.
5. Valores de `tipo`, `gravedad` o `direccion` no permitidos → se descarta
   el aviso.
6. Todo lo descartado se cuenta en el informe (`descartados`), para poder
   medir cuánto inventa el modelo.

## Interfaz

Módulo `continuidad.py` en la raíz, junto a `recuperar.py`:

```python
contexto = reunir_contexto(cur, obra, pasaje, orden, char, modelo_embeddings)
respuesta = consultar_modelo(contexto, pasaje, cliente=None)   # dict
informe = validar(respuesta, contexto, pasaje)                  # Informe
informe = evaluar(cur, obra, pasaje, orden, char, ...)          # las tres
```

`Informe`: `avisos`, `notas`, `descartados`, `hechos_omitidos`. Cada aviso
lleva el texto del hecho y quién lo afirma, no solo su identificador.

Línea de comandos:

```
python continuidad.py --obra=iliada --orden=4 --char=12345 --fichero=pasaje.txt
```

`validar` y la construcción del prompt son funciones puras (sin base de
datos ni API), para poder probarlas en la suite rápida.

## Cómo se mide

Banco fijo: `tests/fijos/continuidad/casos.json`, 27 casos sobre los
Cantos III y IV de la Ilíada, redactados y revisados **antes** de programar
el agente. No se modifica para que el agente lo pase.

| Categoría | Casos | Qué se espera |
|---|---|---|
| Sembrados | 12 | Un pasaje insertado que contradice un hecho anterior (10 de hecho o de estado de personajes, 2 de estado de objetos; uno es una tensión) |
| Dirección | 4 | Un pasaje en el Canto III que choca con algo posterior |
| Controles | 6 | Párrafos originales evaluados en su sitio: ningún aviso |
| Trampas | 5 | Lo que dice o cree un personaje choca con el narrador: ningún aviso |

Un caso con contradicción cuenta como **detectado** si algún aviso cita uno
de los hechos esperados (`<orden>.<índice>` → `posicion_global = orden *
10000 + índice`). Gravedad y dirección se informan aparte, sin afectar al
umbral.

**Umbral de aceptación** (prudente con los avisos: un agente que avisa de
más acaba ignorado):

- detecta al menos **13 de los 16** casos con contradicción (≥ 80 %);
- como mucho **1 aviso** en total entre los 11 controles y trampas;
- el 100 % de los avisos que llegan al informe cumplen las garantías de
  *Validar* (lo comprueba el código).

## Qué le pide al buscador

Este agente es el primer consumidor real del buscador, así que su banco
decide qué buscador híbrido hace falta, en vez de diseñarlo en abstracto.
Para eso, el informe del banco separa los dos posibles fallos:

- **Cobertura del contexto** (sin modelo, sin coste): en cada caso con
  contradicción, ¿llegó al contexto alguno de los hechos esperados, y por
  qué vía? Se puede medir solo con `reunir_contexto`, con `pytest --db`.
- **Uso del contexto** (con modelo): de los casos en que el hecho sí
  llegó, ¿lo citó el agente?

Un caso no detectado se clasifica así: *fallo del buscador* si el hecho no
llegó al contexto; *fallo del agente* si llegó y no lo usó. Solo los
primeros son trabajo del buscador.

Para cada fallo del buscador el informe guarda el motivo observable:
ninguna entidad detectada en el pasaje, la entidad se nombra solo por un
epíteto o una forma compartida, el hecho nombra a la entidad de otra forma
(«el Atrida», «el hijo de Atreo»), o el hecho solo se relaciona por el
tema (una tregua, un juramento) y no por ninguna entidad.

Con ese desglose se decide la especificación del buscador híbrido, y se
compara cada combinación de vías sobre el mismo banco:

| Vía candidata | Qué cubriría |
|---|---|
| Entidades (v1) | Hechos que nombran por su nombre a alguien del pasaje |
| Léxica (palabras clave, BM25 o `tsvector` de Postgres) sobre los hechos | Coincidencias de palabras que no son entidades: «juramentos», «flecha», «espada» |
| Semántica sobre los **hechos** (hoy solo hay embeddings de pasajes) | Relación por el tema, sin palabras en común |
| Pasajes → hechos (los hechos del tramo donde está cada pasaje parecido) | Aprovechar la búsqueda semántica que ya existe |

| Entidades del entorno (las nombradas en el texto que rodea al punto de inserción) | Pasajes cuyo sujeto está implícito: «Y blandía la espada…» tras un párrafo sobre Menelao |

**Medida previa (2026-10-18)**, imitando la vía de entidades con los JSON
del glosario y del canon, sin base de datos: cobertura **15/16**. El único
fallo es S11: el pasaje no nombra a nadie (el sujeto está en el párrafo
anterior), así que no llega ningún hecho. Además, los colectivos
(«aqueos», «troyanos») traen 90-110 hechos casi todos irrelevantes: el
buscador tendrá que **ordenar por relevancia**, no solo filtrar. Estas
dos observaciones son las primeras entradas para la especificación del
buscador híbrido; la medida real se repite con `reunir_contexto`.

Criterio de entrada al buscador de una vía nueva: que suba la cobertura
del contexto sin bajar el umbral del agente por exceso de ruido (más
hechos irrelevantes → más avisos falsos). Hasta medirlo, v1 usa solo la
vía de entidades.

## Ejecutar el banco

El banco se ejecuta con `pytest --db --api tests/continuidad` (o un script
equivalente) y escribe un informe con el resultado de cada caso y las
respuestas en bruto, que se guardan como respuestas grabadas para la suite
rápida. Coste: 27 llamadas por ejecución.

Necesita a la vez la base de datos (WSL) y la API key (Windows): desde
Windows, `WSLENV=ANTHROPIC_API_KEY` pasa la clave al entorno de WSL sin
escribirla en ningún fichero.

### Construcción de cada caso

- `texto` + `tras_verso`: el pasaje es `texto`, y se inserta al final del
  párrafo que empieza por ese número de verso.
- solo `verso` (controles y algunas trampas): el pasaje es el propio
  párrafo original de ese verso, evaluado en su posición (su inicio).
- Las posiciones se calculan sobre el texto del fragmento cargado en el
  almacén, buscando la línea que empieza por `<verso> `.

## Pruebas (a añadir a tests/SPECS.md)

| Spec | Prueba |
|---|---|
| Las entidades del pasaje se detectan por término, alias o variante, como palabra completa y sin distinguir mayúsculas | R |
| Un epíteto o una forma compartida no trae entidades | R, D |
| El contexto incluye hechos posteriores al punto de inserción, con su dirección; entre fragmentos, la dirección es correcta | D |
| El prompt lleva cada hecho con su identificador, su atribución y su dirección | R |
| Un aviso con cita no literal o con un hecho fuera del contexto se descarta | R |
| Un aviso apoyado en un hecho de personaje pasa a notas | R |
| Entre fragmentos, la dirección la fija el código | R |
| Con más hechos que el tope, se omiten los de las entidades más generales y se informa | R |
| Con respuestas grabadas del banco, el informe se reproduce igual | S |
| Cada hecho del contexto indica por qué vía llegó | R |
| Cobertura del contexto en el banco (informativa, sin umbral en v1), con el motivo de cada hecho que no llega | D |
| Banco: ≥ 13/16 detectados, ≤ 1 aviso en controles y trampas; cada fallo clasificado como del buscador o del agente | A + D |

## Datos de prueba

Para que el agente se pueda programar y probar fuera de la máquina local (el
repo público), están publicados como datos fijos, todo de la Ilíada:

- `tests/fijos/textos/iliada_canto_iv.txt`;
- el canon grabado de los Cantos III y IV y el glosario tras el Canto IV
  (`tests/fijos/iliada_cantos_iii_iv/`);
- el banco `tests/fijos/continuidad/casos.json`.

Las pruebas con base de datos cargan esos cuatro cantos como una obra
aparte, para no alterar la base de prueba de los Cantos I-II que usan las
demás pruebas.

## Límites conocidos (para versiones siguientes)

- **Reescritura**: si el pasaje sustituye a texto existente, el canon
  todavía contiene los hechos de la versión antigua, y el agente los vería
  como contradicciones. Para descartarlos hay que saber de qué trozo sale
  cada hecho: que `extraer_canon.py` guarde la posición en caracteres de
  cada hecho. El mismo cambio haría determinista la dirección dentro del
  mismo fragmento.
- **Entidades nombradas solo por epíteto** o por una forma compartida («el
  Atrida») no se detectan: sus hechos solo llegan si los trae la búsqueda
  por embedding.
- **Huecos del glosario** (p.ej. «Tritogenia» como entidad aparte de
  Minerva) se trasladan al agente.
