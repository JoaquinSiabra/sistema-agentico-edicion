"""
calibrar_taxonomia.py

Fase 0a del bootstrapping: antes de extraer hechos canónicos, el sistema
necesita saber qué TIPOS DOCUMENTALES existen en ESTA obra — no una lista
fija inventada a mano (narrativa/enciclopedia/transcripcion/archivo/dialogo,
que es lo que usa hoy extraer_canon.py como categorías estáticas), sino la
taxonomía que la propia obra exhibe, derivada de fragmentos reales.

DIFERENCIA DE FONDO CON calibrar_glosario.py (Fase 0b): en 0b el problema
era que la categoría de un término (persona/concepto/...) es una propiedad
EMERGENTE que solo se ve por repetición de uso a través de varios fragmentos
— un fragmento aislado no basta. Aquí el problema es distinto: el tipo
documental de un fragmento normalmente se delata por marcadores OVERT
dentro del propio fragmento (un encabezado en mayúsculas, una nota a pie,
una etiqueta de hablante tipo "PRESIDENTE:", corchetes de dispositivo). Lo
que SÍ exige ver varios fragmentos a la vez no es la detección en sí, sino
la COBERTURA: para construir la taxonomía completa de la obra hace falta
muestrear fragmentos de partes distintas de la obra, porque un tipo que
solo aparece en una sección no es visible leyendo solo otra.

IMPORTANTE — un fragmento puede contener VARIOS tipos documentales
anidados a la vez (ej.: una narración en primera persona incrustando una
carta, que a su vez incrusta un fragmento de diálogo transcrito). El
prompt pide identificar TODOS los tipos presentes en el lote, no forzar
un tipo único por fragmento.

ESTABILIDAD — A DIFERENCIA DE 0b, AÚN NO SABEMOS SI HAY INESTABILIDAD:
en 0b comprobamos empíricamente que el modelo no converge igual dos veces
sobre términos ambiguos (ver data/exports/discrepancias_calibracion.json
para un caso real). Aquí no lo hemos comprobado
todavía, y es razonable esperar MÁS estabilidad porque los marcadores son
formales y explícitos, no inferencias de uso. Por eso esta v1 NO reconcilia
automáticamente: ejecuta N pasadas independientes (por defecto 1) y las
guarda todas en bruto, sin fusionarlas. Si al comparar varias pasadas a
mano aparece la misma inestabilidad que en 0b (nombres distintos para el
mismo tipo, fronteras que se mueven), entonces — y solo entonces — se le
añade aquí una reconciliación como la de calibrar_glosario.py. Pero ojo:
ahí la reconciliación fue posible porque "categoría de un término" es un
voto sobre un conjunto CERRADO de opciones (persona/lugar/...). Aquí cada
pasada propone un conjunto ABIERTO de nombres de tipo, así que fusionar
pasadas no puede ser un Counter() de cadenas exactas — haría falta una
pasada de reconciliación que sea ELLA MISMA una llamada al modelo (agrupar
semánticamente nombres distintos que designan el mismo tipo). No se
implementa hasta confirmar que hace falta.

SALIDA: data/exports/taxonomia_pasada_N.json (una por pasada, sin fusionar)
y, si solo se pidió una pasada (--n=1, el valor por defecto), además
data/exports/tipos_documentales_obra.json como copia directa de esa única
pasada — ese es el fichero que en el futuro leerá extraer_canon.py en vez
de su lista fija actual.

Uso:
    uv run python src/calibrar_taxonomia.py [--n=1] data/novelas/fragmento_1.txt data/novelas/fragmento_2.txt ...
"""

import json
import sys
from pathlib import Path

import anthropic

MODELO = "claude-sonnet-4-6"

PROMPT_SISTEMA = """Eres un analista de tipografía documental especializado en \
textos de ficción con estructura narrativa compleja (manuscritos que incrustan \
documentos de distinta naturaleza: transcripciones, actas, notas, anotaciones \
de dispositivo, etc.). Vas a recibir varios fragmentos de UNA MISMA novela, \
separados por marcadores "=== FRAGMENTO N ===".

Tu tarea: identificar TODOS los tipos documentales distintos que aparecen en \
el conjunto de fragmentos. Por "tipo documental" no nos referimos al GÉNERO \
de la novela ni a su contenido temático, sino al MARCO FORMAL de cada \
segmento de texto: qué clase de objeto textual es, en el universo de la \
obra (¿transcripción de voz?, ¿acta de una reunión?, ¿nota de enciclopedia?, \
¿anotación automática de un dispositivo?, ¿noticia retransmitida?, ¿narración \
convencional en tercera persona?, ¿monólogo en primera persona?).

REGLAS:
- Un mismo fragmento puede contener VARIOS tipos documentales anidados (por \
ejemplo, una narración en primera persona puede incrustar un acta de \
asamblea, que a su vez incrusta diálogo transcrito con etiquetas de \
hablante). Identifica TODOS los tipos presentes, sin forzar un tipo único \
por fragmento.
- NO distingas dos tipos solo porque el encabezado o el escenario concreto \
cambia (p.ej. dos cartas de personajes distintos, con encabezados distintos, \
pero la MISMA clase de objeto formal —correspondencia epistolar— no son dos \
tipos distintos). Fíjate en la FUNCIÓN y la FORMA, no en el rótulo textual \
exacto.
- SÍ distingue dos tipos si su función formal es distinta aunque ambos sean \
"documentos insertados": una carta personal (voz individual, fórmulas de \
saludo y despedida) no es lo mismo que una entrada de diario íntimo (fecha, \
primera persona sin destinatario) ni que un recorte de periódico (voz \
editorial impersonal, titular).
- Basa cada tipo en evidencia textual observable: encabezados en mayúsculas, \
marcas de puntuación específicas (corchetes, asteriscos), etiquetas de \
hablante, notas a pie de página, cambios de persona gramatical o de número \
(singular/plural) en el narrador, fórmulas de apertura o cierre repetidas \
("Tal dijo.", "tres días después...", etc.).
- Para cada tipo, describe en "voz" quién o qué instancia "habla" ese texto: \
un personaje concreto narrando en primera persona, una voz colectiva, un \
dispositivo o sistema no humano, un narrador omnisciente en tercera persona, \
una institución (sin autor individual atribuible), etc.
- Si la evidencia es insuficiente o el caso es dudoso, usa confianza "baja" \
y explica la duda en vez de forzar una distinción.

Devuelve EXCLUSIVAMENTE un JSON con esta forma, sin texto antes ni después, \
sin markdown:

{
  "tipos_documentales": [
    {
      "nombre": "",
      "descripcion": "",
      "voz": "",
      "marcadores_formales": "",
      "fragmentos_evidencia": [],
      "evidencia": "breve cita o descripción del patrón de uso observado",
      "confianza": "alta | media | baja"
    }
  ]
}
"""


def leer_fragmento(ruta: str) -> str:
    return Path(ruta).read_text(encoding="utf-8")


def construir_entrada(rutas: list[str]) -> str:
    partes = []
    for i, ruta in enumerate(rutas, start=1):
        texto = leer_fragmento(ruta)
        partes.append(f"=== FRAGMENTO {i} ({Path(ruta).name}) ===\n{texto}")
    return "\n\n".join(partes)


def calibrar(rutas: list[str]) -> dict:
    cliente = anthropic.Anthropic()
    entrada = construir_entrada(rutas)

    print(f"Enviando {len(rutas)} fragmentos ({len(entrada)} caracteres totales)...")

    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=8000,
        system=PROMPT_SISTEMA,
        messages=[{"role": "user", "content": entrada}],
    )

    texto = respuesta.content[0].text.strip()
    texto = texto.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        return json.loads(texto)
    except json.JSONDecodeError as e:
        print(f"\n--- ERROR AL PARSEAR JSON: {e} ---")
        print(f"stop_reason: {respuesta.stop_reason}")
        print(texto)
        raise


def calibrar_n_veces(rutas: list[str], n: int) -> list[dict]:
    """Ejecuta calibrar() n veces de forma independiente. A diferencia de
    calibrar_glosario.py, AQUÍ NO SE RECONCILIA — cada pasada se guarda en
    bruto para comparación manual. Ver docstring del módulo: el motivo es
    que no hemos confirmado todavía que haga falta, y si hace falta, la
    reconciliación de un taxonomía de nombres abiertos no puede ser un
    simple recuento de cadenas."""
    resultados = []
    for i in range(n):
        print(f"\n--- Pasada {i + 1}/{n} ---")
        try:
            resultados.append(calibrar(rutas))
        except json.JSONDecodeError:
            print(f"Pasada {i + 1} descartada por error de parseo (detalle arriba).")
            continue

    if not resultados:
        print("\nNinguna pasada se completó correctamente. Abortando.")
        sys.exit(1)
    if len(resultados) < n:
        print(f"\nAviso: solo {len(resultados)} de {n} pasadas se completaron con éxito.")
    return resultados


def guardar(datos, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    n = len(datos.get("tipos_documentales", [])) if isinstance(datos, dict) else "—"
    print(f"Guardado en {ruta} ({n} tipos documentales)")


def parsear_args(args: list[str]) -> tuple[int, list[str]]:
    n = 1
    rutas = []
    for arg in args:
        if arg.startswith("--n="):
            n = int(arg.split("=", 1)[1])
        else:
            rutas.append(arg)
    return n, rutas


def main():
    if len(sys.argv) < 2:
        print(
            "Uso: uv run python src/calibrar_taxonomia.py [--n=1] "
            "<fragmento1.txt> [fragmento2.txt ...]"
        )
        sys.exit(1)

    n, rutas = parsear_args(sys.argv[1:])
    if not rutas:
        print("No se ha indicado ningún fragmento.")
        sys.exit(1)

    resultados = calibrar_n_veces(rutas, n)

    for i, resultado in enumerate(resultados, start=1):
        guardar(resultado, Path(f"data/exports/taxonomia_pasada_{i}.json"))

    if len(resultados) == 1:
        guardar(resultados[0], Path("data/exports/tipos_documentales_obra.json"))
        print(
            "\nUna sola pasada: copiada directamente a tipos_documentales_obra.json."
        )
    else:
        print(
            f"\n{len(resultados)} pasadas guardadas SIN reconciliar — "
            "compáralas a mano en data/exports/taxonomia_pasada_*.json. "
            "Si los nombres y fronteras coinciden razonablemente entre "
            "pasadas, copia la que prefieras como tipos_documentales_obra.json. "
            "Si hay inestabilidad real (como en 0b), avisa y añadimos "
            "una reconciliación dedicada."
        )


if __name__ == "__main__":
    main()
