# Agente de Voz y edición — especificación v1 (borrador)

> **Borrador para revisar.** El banco (`docs/agente_voz_casos.json`) tampoco
> está fijado: se fija, y se mueve a `tests/fijos/voz/casos.json`, cuando
> esta especificación se dé por buena y **antes** de programar el agente.

Herramienta del **autor**, como el agente de Continuidad: dado un pasaje que
se va a insertar en la obra, avisa de dónde se aparta de la voz de la obra.
No corrige estilo, no reescribe y no opina sobre si el pasaje es bueno.

## Qué es «la voz» en la v1, y qué no

La primera idea era vigilar la persona del narrador (tercera), el tiempo
verbal (pasado) y el registro. La propia Ilíada lo desmiente:

| Rasgo que parecería una norma | Dónde la obra lo incumple |
|---|---|
| El narrador no habla en primera persona | II 484: «Decidme ahora, Musas […] Pero mencionaré los caudillos y las naves todas.» |
| El narrador cuenta en pasado | III 369: «Dice, y arremetiendo á Paris, cógele por el casco […] y le arrastra» (presente histórico) |
| El narrador no se dirige a nadie | IV 127: «No se olvidaron de ti, oh Menelao» (apóstrofe); IV 223: «Entonces no hubieras visto…» (al oyente) |

Un agente que avisara de esto acabaría ignorado, igual que uno de
continuidad que tomara por error la mentira de un personaje. De ahí la
regla de la v1, que hace el papel de «solo el narrador funda un aviso»:

**Solo puede fundar un aviso lo que la obra hace siempre o no hace nunca,
medido sobre la propia obra.** Hay dos clases:

1. **Normas de la edición** (deterministas, sin modelo): formas que la obra
   escribe siempre de una manera. En la Segalá: «á» 661 veces y «a» ninguna;
   «ó» 47 y «o» ninguna; «fué» 31 y «fue» ninguna; el diálogo se abre
   siempre con «», nunca con raya ni comillas inglesas (la raya aparece 53
   veces, siempre pegada a la palabra, como inciso o en los títulos).
2. **Léxico ajeno a la obra** (modelo, con un filtro determinista previo):
   palabras cuya raíz no aparece en ninguna parte de la obra y que,
   además, el modelo juzga anacrónicas, coloquiales o técnicas para esta
   obra («reloj», «kilómetros», «estrés»). La palabra citada tiene que
   estar entre los candidatos del filtro: el modelo no puede avisar de una
   palabra que la obra sí usa.

Lo demás (persona, tiempo verbal, apóstrofes, ritmo de la frase, registro
de una construcción con palabras que la obra sí usa) puede ir, como mucho,
a **notas**. Nunca a avisos.

| Tema | Decisión v1 |
|---|---|
| Qué evalúa | Normas de la edición y léxico ajeno a la obra, en todo el pasaje (narrador y diálogos: un anacronismo lo es en boca de cualquiera) |
| Fuera de alcance | Persona, tiempo verbal, apóstrofes, caracterización («X no hablaría así»), ritmo, calidad |
| Horizonte | Toda la obra, anterior y posterior: el vocabulario de la obra entera |
| Entrada | Un **pasaje nuevo** y su **posición** (fragmento y carácter), como Continuidad |
| Gravedad | **Ruptura** (no puede estar así: una norma rota, un anacronismo claro) y **extrañeza** (posible, pero conviene revisarlo: «minutos» en un mundo sin relojes) |
| Normas | Siempre ruptura: no las decide el modelo |
| Coste | Una llamada como mucho; **ninguna** si el pasaje no tiene candidatos léxicos |

## Flujo

```
pasaje + posición
   │
   ├─ 1. perfil_de_voz (determinista, base de datos; una vez por obra)
   │      normas de la edición + raíces del vocabulario de la obra
   │
   ├─ 2. medir (determinista, sin base de datos ni API)
   │      normas rotas → avisos directos
   │      palabras cuya raíz no está en la obra → candidatos léxicos
   │
   ├─ 3. consultar_modelo (una llamada, solo si hay candidatos)
   │      → avisos léxicos y notas en JSON
   │
   └─ 4. validar (determinista) → Informe
```

### 1. Perfil de voz de la obra

Se calcula sobre el texto de todos los fragmentos de la obra en el almacén.
Del texto se quita antes el aparato de la edición que no es voz: los
números de verso al comienzo de cada párrafo, los títulos de canto y los
pies de ilustración. En la Segalá, estos dos últimos son las líneas sin
número de verso, salvo las que empiezan en minúscula: esas continúan un
párrafo cortado por una ilustración (en el Canto III, el del verso 111).

- **Normas ortográficas.** Para cada par de formas variables de la lista
  fija (`á/a`, `ó/o`, `é/e`, `fué/fue`, `dió/dio`, `vió/vio`), como
  palabras completas y sin distinguir mayúsculas: si el par aparece al
  menos 20 veces en la obra y una forma supera el 99 %, la otra es una
  norma rota. La lista es de pares, no de respuestas: en una novela
  moderna la norma sale al revés y el aviso sería para «fué». Con la
  Segalá, `vió/vio` no llega a 20 apariciones y no da norma.
- **Marcas de diálogo.** Cuántas veces la obra abre un parlamento con «,
  con raya de diálogo (una raya al comienzo de línea o precedida de
  espacio) y con comillas inglesas (" o “). Si una marca supera el 99 % y
  hay al menos 20 parlamentos, las otras dos son normas rotas.
- **Vocabulario.** Las raíces de todas las palabras de la obra, con
  Snowball para el español en Python (`snowballstemmer`): el mismo
  algoritmo que la configuración `spanish` de Postgres que usa la vía
  léxica del buscador. En Python y no en Postgres para que el perfil, la
  medida y la validación se prueben en la suite rápida; la base de datos
  solo aporta los textos de la obra. Por raíz y no por forma: por forma,
  los párrafos originales tienen de 12 a 35 palabras que no aparecen en el
  resto de la obra (flexiones y hápax); por raíz, de 4 a 20.

Si el pasaje evaluado es texto que ya está en la obra en ese punto (los
controles del banco), su propio tramo se excluye del vocabulario: si no,
ninguna de sus palabras sería nueva y los controles no medirían nada.

### 2. Medir

- **Normas rotas**: cada aparición en el pasaje de una forma o marca que
  la obra no usa es un aviso de **norma**, gravedad ruptura, con la
  evidencia («la obra escribe «á» 661 veces y «a» ninguna»). Sin modelo.
- **Candidatos léxicos**: las palabras del pasaje cuya raíz no está en el
  vocabulario de la obra. Se ignoran las formas de las normas (ya tienen
  su aviso) y las palabras en mayúscula que no van a comienzo de frase
  (nombres propios: un personaje nuevo es cosa de Continuidad).

### 3. Consultar al modelo

Solo si hay candidatos. Una llamada, el mismo modelo que el resto de fases
(`MODELO` de `calibrar_glosario.py`). El prompt lleva el pasaje, los
candidatos, el texto que rodea al punto de inserción (≈1500 caracteres
antes y después) y las reglas:

- un aviso solo puede ser sobre una palabra de la lista de candidatos;
- que una palabra no aparezca en la obra no basta: muchas de las que el
  filtro deja pasar son correctas para la obra (flexiones raras, términos
  de época como «yelmo»). Avisa solo de las anacrónicas, coloquiales o
  técnicas **para esta obra**;
- definiciones de ruptura y extrañeza, con un ejemplo de cada una;
- la cita es un fragmento literal del pasaje que contiene la palabra;
- persona, tiempo verbal, apóstrofes o registro de una construcción van a
  `notas`, nunca a avisos;
- si no hay nada, listas vacías.

Respuesta, solo JSON:

```json
{
  "avisos": [
    {
      "palabra": "reloj",
      "cita": "mirando de cuando en cuando su reloj",
      "rasgo": "anacronismo | coloquialismo | tecnicismo",
      "gravedad": "ruptura | extrañeza",
      "explicacion": "una o dos frases"
    }
  ],
  "notas": [{"cita": "…", "explicacion": "…"}]
}
```

### 4. Validar (garantías deterministas)

1. Un aviso cuya `cita` no aparezca literalmente en el pasaje (con espacios
   normalizados) **se descarta**.
2. Un aviso cuya `palabra` no esté entre los candidatos, o no esté dentro
   de su `cita`, **se descarta**.
3. Un aviso sobre una forma de norma («a», «fue») **se descarta**: las
   normas no las decide el modelo.
4. `rasgo` o `gravedad` no permitidos → **se descarta**.
5. Dos avisos sobre la misma palabra se quedan en uno (el de mayor gravedad).
6. Todo lo descartado se cuenta en el informe (`descartados`).

## Interfaz

Módulo `src/voz.py`, junto a `continuidad.py`:

```python
perfil = perfil_de_voz(cur, obra)                       # una vez por obra
medida = medir(pasaje, perfil, excluir=None)            # normas + candidatos
respuesta = consultar_modelo(medida, contexto, pasaje, cliente=None)
informe = validar(respuesta, medida, pasaje)            # Informe
informe = evaluar(cur, obra, pasaje, orden, char, ...)  # todo
```

`Informe`: `avisos` (cada uno con `clase`: norma o léxico, la cita, la
gravedad y la evidencia), `notas`, `descartados`, `llamadas` (0 o 1).

`perfil_de_voz` también acepta los textos directamente
(`perfil_de_textos(textos)`), para que medir, el perfil y la validación se
prueben en la suite rápida con `tests/fijos/textos/`.

Línea de comandos:

```
python src/voz.py --obra=iliada --orden=4 --char=12345 --fichero=pasaje.txt
```

## Cómo se mide

Banco: `docs/agente_voz_casos.json` (borrador), 22 casos sobre los Cantos
III y IV, con el vocabulario de los Cantos I-IV.

| Categoría | Casos | Qué se espera |
|---|---|---|
| Norma | 4 | Un aviso de norma: «a» y «o» sin tilde, «fue» y «dio», raya de diálogo, comillas inglesas |
| Léxico | 6 | Un aviso léxico sobre la palabra sembrada (cinco rupturas y una extrañeza) |
| Controles | 6 | Párrafos originales de narrador, evaluados en su sitio: ningún aviso |
| Trampas | 6 | Lo que la obra sí hace: presente histórico, apóstrofe, segunda persona al oyente (originales), invocación a las Musas en primera persona, una palabra de época que no está en la obra («yelmo») y un diálogo en presente y primera persona: ningún aviso |

**Umbral de aceptación:**

- **normas: 4 de 4 y ningún aviso de norma en controles y trampas.** Es
  determinista: se comprueba en la suite rápida, sin API;
- **léxico: al menos 5 de 6** casos detectados (el aviso cita la palabra
  sembrada; la gravedad se informa aparte);
- **como mucho 1 aviso** léxico en total entre los 12 controles y trampas;
- el 100 % de los avisos cumplen las garantías de *Validar*.

### Medir sin API

Como en Continuidad, el banco separa los dos posibles fallos:

- **Cobertura del filtro** (sin modelo): ¿está la palabra sembrada entre
  los candidatos? Si no, es un fallo del filtro, no del agente.
- **Ruido del filtro** (sin modelo): cuántos candidatos llegan en los
  controles y las trampas. Son las palabras que el modelo tiene que saber
  dejar pasar: cuantas más, más ocasiones de avisar de más.

**Medida previa (2026-11-01)**, con los textos fijos y el lematizador
Snowball en Python (el mismo algoritmo que la configuración `spanish` de
Postgres), sin base de datos:

| | Resultado |
|---|---|
| Normas | 4/4 detectadas; ninguna en controles ni trampas |
| Cobertura léxica | 6/6: la palabra sembrada está siempre entre los candidatos, con 0 o 1 palabra más |
| Ruido en controles | 4, 7, 18, 8, 15 y 9 candidatos (aguamanos, almo, daga, penacho, rodelas…) |
| Ruido en trampas | 20, 7 y 7 en los originales; 1, 2 y 0 en los insertados |

El riesgo del agente está en el ruido, no en la cobertura: en el control
C03 el modelo tiene que dejar pasar 18 palabras, entre ellas «bovino»,
«lascivo» o «dieciséis». Si el banco muestra avisos de más, la primera
palanca es el filtro (por ejemplo, ignorar los hápax con terminación
verbal antigua, «rompiéronse», «cógele»), no el prompt.

## Pruebas (a añadir a tests/SPECS.md)

| Spec | Prueba |
|---|---|
| El perfil ignora números de verso, títulos y pies de ilustración | R |
| Una norma ortográfica sale de la obra, en la dirección que la obra usa, y solo con 20 apariciones y más del 99 % | R |
| La raya pegada a la palabra (inciso) no cuenta como marca de diálogo | R |
| Normas del banco: 4/4 y ninguna en controles y trampas | R (con `tests/fijos/textos/`) |
| Los candidatos se comparan por raíz, sin nombres propios ni formas de norma | R |
| Un pasaje que ya está en la obra se mide sin su propio tramo en el vocabulario | R |
| Sin candidatos no se llama al modelo | R (con el cliente falso) |
| Un aviso con cita no literal, con una palabra fuera de los candidatos o sobre una forma de norma se descarta | R |
| Cobertura y ruido del filtro en el banco (informativo) | R |
| Con respuestas grabadas del banco, el informe se reproduce igual | S |
| Banco: léxico ≥ 5/6, ≤ 1 aviso léxico en controles y trampas | A + D |

## Límites conocidos

- **Registro sin palabras nuevas.** «Pero claro, Héctor no estaba para
  nada de acuerdo» usa palabras que la obra tiene; la v1 solo puede dar
  una nota. Si hace falta, una v2 compararía construcciones, no palabras.
- **Una obra corta tiene poco vocabulario**: con pocos cantos, cualquier
  palabra rara es candidata y el ruido sube. Conviene medir el ruido con
  cada canto nuevo.
- **Formas enclíticas** («ciñóse», «rompióle»): Snowball no las reduce a la
  raíz del verbo, así que llegan como candidatas aunque el verbo esté en
  la obra.
- **La voz de cada personaje** (caracterización) queda fuera, como en
  Continuidad.

## Decisiones (2026-11-01)

Resueltas las dudas del primer borrador:

1. **Umbrales de las normas**: 20 apariciones y más del 99 %. Con la
   Segalá dan normas limpias para «á», «ó», «é», «fué» y «dió»; `vió/vio`
   (3 apariciones) no cuenta.
2. **Raíz en Python** (`snowballstemmer`), no en Postgres: todo lo
   determinista (normas, candidatos, cobertura y ruido) entra en la suite
   rápida y en la CI. Al implementarlo, `snowballstemmer` se añade a
   `requirements.txt` y a las dependencias del workflow de pruebas.
3. **«minutos» (L06) es extrañeza**: español de 1908, pero no del mundo
   del poema. Para el umbral cuenta igual que una ruptura; la gravedad se
   informa aparte.
4. **Nombre: «Voz y edición»**, porque la v1 evalúa las normas de la
   edición y la voz medible de la obra, no persona ni tiempo. El módulo
   sigue siendo `src/voz.py`.

Pendiente: revisar el banco y fijarlo (moverlo a `tests/fijos/voz/casos.json`)
antes de programar.
