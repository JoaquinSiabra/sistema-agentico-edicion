# Respuestas reales de Claude sobre la Ilíada (Cantos III y IV)

Continuación de `../iliada_cantos_i_ii/`: lo que devolvió `claude-sonnet-4-6`
al incorporar los Cantos III (2026-10-12) y IV (2026-10-18) de la traducción
de Segalá con la calibración incremental. Son los datos del agente de
Continuidad (`docs/agente_continuidad.md`, banco en `../continuidad/`).

| Fichero | Qué es |
|---|---|
| `calibracion_iliada_canto_iii_pasada_{1,2,3}.json` | Respuestas en bruto de la calibración incremental del Canto III (prompt anterior a la separación de epítetos: sin campo `epitetos`) |
| `calibracion_iliada_canto_iv_pasada_{1,2,3}.json` | Ídem del Canto IV, ya con `epitetos` |
| `personajes_obra.json`, `lugares_obra.json`, `glosario_obra.json`, `discrepancias_calibracion.json` | Glosario de la obra tras el Canto IV (ver abajo cómo se llegó a él) |
| `respuestas_canon/000{3,4}.json` | Respuesta en bruto de `extraer_canon.py` para los Cantos III y IV (sin `orden_fragmento` ni `ruta_fragmento_origen`) |

Cómo se llegó al glosario, en orden:

1. Glosario de los Cantos I-II recalibrado y Canto III incorporado (2026-10-12).
2. Saneado con `calibrar_glosario.py --sanear`: 4 formas compartidas
   retiradas a discrepancias; 53 epítetos revisados a mano y pasados con
   `--epitetos` de alias a `epitetos`.
3. Canto IV incorporado, con la corrección del orden de `incorporar`
   (PR #7).

Retoque manual: en `respuestas_canon/0004.json` el modelo escribió
«Eritogenia» en la lista de personajes; se corrigió a «Tritogenia», que es
lo que dice el texto. Todo lo demás es la respuesta tal cual.

**No se regeneran.** Los índices de los hechos (`<canto>.<índice>`) son los
que usa el banco de casos de Continuidad.
