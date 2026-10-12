# Respuestas reales de Claude sobre la Ilíada (Cantos I y II)

Copias congeladas de lo que devolvió `claude-sonnet-4-6` el 2026-10-03 al
ejecutar las fases 0a, 0b y 1 sobre los Cantos I y II de `tests/fijos/textos/`
(traducción de Segalá). Sirven para probar todo lo que
el código hace con las respuestas del modelo sin llamar a la API.

| Fichero | Qué es |
|---|---|
| `calibracion_pasada_{1,2,3}.json` | Respuestas en bruto de las 3 pasadas de `calibrar_glosario.py` |
| `personajes_obra.json`, `lugares_obra.json`, `glosario_obra.json`, `discrepancias_calibracion.json` | Lo que `calibrar_glosario.py` generó a partir de esas 3 pasadas |
| `taxonomia_pasada_1.json` | Respuesta en bruto de `calibrar_taxonomia.py` (`--n=1`) |
| `tipos_documentales_obra.json` | Copia de la pasada única, como la escribe `calibrar_taxonomia.py` |
| `respuestas_canon/000N.json` | Respuesta en bruto de `extraer_canon.py` para el canto N (sin `orden_fragmento` ni `ruta_fragmento_origen`, que añade el script) |

**No se regeneran.** Si cambia un prompt o el modelo, se graban respuestas
nuevas en otra carpeta: estas documentan cómo era la salida en esa fecha.
