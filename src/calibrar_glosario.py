"""
calibrar_glosario.py

Fase 0b del bootstrapping: antes de extraer hechos canónicos, el sistema
necesita saber qué términos inventados de ESTA obra son personajes, lugares,
pueblos/razas, objetos, organizaciones o conceptos abstractos.

A diferencia de extraer_canon.py, este script necesita ver VARIOS fragmentos
a la vez en una sola llamada: el patrón que delata si un término es un
concepto abstracto o una persona solo es visible por repetición across
fragments, no dentro de uno aislado. Por la misma razón, el modelo
tiene que ver y clasificar TODO junto en una sola pasada (no puede decidir
"esto es un concepto" sin comparar contra los nombres de persona reales
del mismo lote).

ESTABILIDAD: en la frontera de ambigüedad el modelo no converge igual dos
veces sobre el mismo texto (lo comprobamos empíricamente). Por eso el
script ejecuta el lote N veces (por defecto 3) de forma independiente y
RECONCILIA por MAYORÍA: si una categoría reúne más votos que cualquier
otra entre las pasadas que mencionan el término, esa categoría se acepta
como estable y el disenso (las pasadas que dijeron otra cosa) se registra
en el propio dato, sin ocultarlo. Solo si hay empate sin mayoría clara el
término se separa a un fichero de discrepancias para revisión humana.

VARIANTES Y FUSIÓN: el modelo no segmenta los nombres de forma consistente
entre pasadas (a veces "Agamenón", a veces "Atrida Agamenón"), y el mismo
problema aparece en colectivos (un mismo pueblo referido como "aqueos",
"aquivos", "argivos" o "dánaos" según el pasaje). En vez de depender de
que distintas pasadas extraigan formas distintas por azar, se le pide al
modelo que AUTOREPORTE las variantes del mismo referente dentro de su
propia pasada (campo "variantes"). Tras reconciliar, fusionar_alias() usa
ese autoreporte — válido en cualquier categoría, porque es un juicio
semántico del propio modelo — para consolidar entradas. Solo en personajes
se añade además una subcadena de respaldo (nombre corto contenido en
nombre completo): en otras categorías una subcadena suele señalar una
entidad relacionada pero DISTINTA (p.ej. un lugar vs. una institución que
lleva su nombre), así que ahí no se activa.

IMPORTANTE: no se le da al modelo ningún glosario humano preexistente.
Tiene que inferir la categoría únicamente de cómo se usa el término en el
propio texto (¿recibe trato personal? ¿aparece en fórmulas fijas? ¿alguna
nota a pie de página lo define?).

SALIDA: el resultado reconciliado se reparte en CUATRO ficheros:
    data/exports/personajes_obra.json         (categoria == persona, con
                                                alias fusionados)
    data/exports/lugares_obra.json            (categoria == lugar)
    data/exports/glosario_obra.json           (objeto, organizacion, pueblo,
                                                concepto_abstracto, incierto)
    data/exports/discrepancias_calibracion.json (términos en empate, sin
                                                mayoría clara entre pasadas)
Además guarda cada pasada en bruto en data/exports/calibracion_pasada_N.json
por si hace falta auditar qué dijo cada una.

Uso:
    uv run python src/calibrar_glosario.py [--n=3] data/novelas/fragmento_1.txt data/novelas/fragmento_2.txt ...
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import anthropic

MODELO = "claude-sonnet-4-6"

PROMPT_SISTEMA = """Eres un lingüista especializado en detectar terminología \
inventada en textos de ficción especulativa. Vas a recibir varios fragmentos \
de UNA MISMA novela, separados por marcadores "=== FRAGMENTO N ===".

Tu tarea: identificar TODOS los términos capitalizados o inventados que se \
REPITEN en más de un fragmento (o varias veces dentro de uno) — sin excepción, \
incluidos los nombres de persona obvios. No descartes un término solo porque \
su categoría te parezca evidente: el listado completo es necesario para que un \
paso posterior reparta personajes, lugares y conceptos en registros distintos. \
Clasifica cada uno según cómo se usa, no según lo que suene plausible.

Para cada término, basa la clasificación EXCLUSIVAMENTE en evidencia textual \
observable:
- ¿Se le habla directamente, o habla él mismo (verbos de dicho, vocativo)?
  -> indicio de persona.
- ¿Aparece como ubicación de una acción ("en X", "hacia X")?
  -> indicio de lugar.
- ¿Se describe como especie, gentilicio o colectivo étnico/biológico al que \
  se pertenece por nacimiento, sin estructura jurídica de membresía \
  (se habla DE ellos en plural, no se les nombra como parte contratante \
  con representantes)? -> indicio de pueblo.
- ¿Es una entidad con estructura jurídica o de membresía voluntaria/contractual \
  (firma convenios, tiene representantes, opera como parte contratante)?
  -> indicio de organizacion.
- ¿Se posee, se solicita, se declara, aparece en fórmulas jurídicas o
  rituales fijas, sin verbo de habla ni trato personal?
  -> indicio de concepto_abstracto u objeto.
- ¿Hay una nota a pie de página o entrada de enciclopedia interna que lo
  defina explícitamente? -> usa esa definición como evidencia fuerte.

Categorías permitidas: persona, lugar, pueblo, objeto, organizacion, concepto_abstracto.

Si la evidencia es insuficiente o contradictoria, usa la categoría "incierto" \
y explica por qué en lugar de forzar una clasificación.

VARIANTES: si el MISMO referente aparece nombrado de más de una forma en el \
texto (p.ej. un nombre de persona solo vs. con patronímico o epíteto, o el \
nombre corto de un lugar vs. su denominación completa), usa como "termino" \
la forma más completa que hayas visto y lista las OTRAS formas observadas \
en "variantes" — sin repetir ahí la que ya pusiste en "termino". No \
incluyas entidades relacionadas pero DISTINTAS (p.ej. no pongas un lugar \
como variante de una institución que opera allí: son dos referentes \
distintos, no dos nombres del mismo). Si solo hay una forma observada, \
deja "variantes" vacía.

Devuelve EXCLUSIVAMENTE un JSON con esta forma, sin texto antes ni después, \
sin markdown:

{
  "glosario": [
    {
      "termino": "",
      "categoria": "",
      "variantes": [],
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
    """Ejecuta calibrar() n veces de forma independiente sobre el mismo lote.
    Si una pasada falla (p.ej. JSON truncado), se descarta esa pasada sin
    perder las que ya se completaron — cada pasada es una llamada de pago,
    no tiene sentido tirarlas todas por un fallo aislado."""
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


ORDEN_CONFIANZA = {"alta": 3, "media": 2, "baja": 1}


def reconciliar(resultados: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Agrupa las entradas de N pasadas por término. La categoría con más
    votos entre las pasadas que mencionan el término se acepta como
    estable; las pasadas que dijeron otra cosa se registran como "disenso"
    en el propio dato, sin ocultarlas. Solo si hay empate sin mayoría clara
    el término se separa a discrepancias."""
    por_termino: dict[str, list[tuple[int, dict]]] = {}
    for i, resultado in enumerate(resultados):
        for entrada in resultado.get("glosario", []):
            termino = (entrada.get("termino") or "").strip()
            if not termino:
                continue
            por_termino.setdefault(termino, []).append((i, entrada))

    estables: dict[str, dict] = {}
    discrepancias: list[dict] = []

    for termino, pares in por_termino.items():
        indices = [i for i, _ in pares]
        entradas = [e for _, e in pares]
        conteo = Counter(e.get("categoria") for e in entradas)
        categoria_mayoritaria, votos = conteo.most_common(1)[0]
        hay_empate = sum(1 for _, v in conteo.most_common() if v == votos) > 1

        if hay_empate:
            discrepancias.append(
                {
                    "termino": termino,
                    "pasadas_totales": len(resultados),
                    "pasadas_que_lo_mencionan": len(entradas),
                    "motivo": "empate sin mayoría clara",
                    "categorias_propuestas": [
                        {
                            "categoria": e.get("categoria"),
                            "confianza": e.get("confianza"),
                            "evidencia": e.get("evidencia"),
                        }
                        for e in entradas
                    ],
                }
            )
            continue

        candidatas = [e for e in entradas if e.get("categoria") == categoria_mayoritaria]
        entrada_elegida = min(
            candidatas, key=lambda e: ORDEN_CONFIANZA.get(e.get("confianza"), 0)
        )
        disenso = [
            {"categoria": e.get("categoria"), "confianza": e.get("confianza")}
            for e in entradas
            if e.get("categoria") != categoria_mayoritaria
        ]
        variantes_unidas = sorted(
            {v for e in entradas for v in (e.get("variantes") or []) if v and v != termino}
        )

        estables[termino] = {
            **entrada_elegida,
            "categoria": categoria_mayoritaria,
            "variantes": variantes_unidas,
            "pasadas_que_lo_mencionan": len(entradas),
            "pasadas_totales": len(resultados),
            "pasadas_indices": sorted(i + 1 for i in indices),
            "disenso": disenso,
        }

    return estables, discrepancias


def guardar(datos, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False), encoding="utf-8")
    n = len(datos) if isinstance(datos, list) else "—"
    print(f"Guardado en {ruta} ({n} entradas)")


def repartir(resultado: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Separa la lista plana en tres listas: personajes, lugares y todo lo
    demás (objeto, organizacion, pueblo, concepto_abstracto, incierto), que
    es lo que de verdad llamamos "glosario"."""
    personajes, lugares, glosario = [], [], []
    for entrada in resultado.get("glosario", []):
        categoria = entrada.get("categoria")
        if categoria == "persona":
            personajes.append(entrada)
        elif categoria == "lugar":
            lugares.append(entrada)
        else:
            glosario.append(entrada)
    return personajes, lugares, glosario


def fusionar_alias(entradas: list[dict], permitir_substring: bool = False) -> list[dict]:
    """Fusiona entradas que refieren al mismo término real.

    Señal principal: el campo "variantes" autoreportado por el modelo —
    fiable en cualquier categoría porque es un juicio semántico del propio
    modelo sobre qué cuenta como "el mismo referente" (p.ej. un nombre
    corto y su forma con patronímico/epíteto para la misma persona).

    Señal de respaldo (permitir_substring=True): si el término de una
    entrada aparece como palabra completa dentro del término de otra, se
    fusionan. Solo se activa para personajes por defecto — en otras
    categorías una subcadena suele señalar una entidad relacionada pero
    DISTINTA (p.ej. un lugar vs. una institución que lleva su nombre),
    así que ahí basta con las variantes autoreportadas."""
    pendientes = []
    for e in entradas:
        e = dict(e)
        propias = {v for v in (e.get("variantes") or []) if v and v != e["termino"]}
        e["alias"] = sorted(propias)
        pendientes.append(e)

    pendientes.sort(key=lambda e: -len(e["termino"]))
    fusionados: list[dict] = []

    for entrada in pendientes:
        termino = entrada["termino"]
        contenedor = None
        for f in fusionados:
            if termino == f["termino"]:
                continue
            es_variante_reportada = termino in f.get("alias", []) or f[
                "termino"
            ] in entrada.get("alias", [])
            es_substring = permitir_substring and re.search(
                r"\b" + re.escape(termino) + r"\b", f["termino"]
            )
            if es_variante_reportada or es_substring:
                contenedor = f
                break

        if contenedor is not None:
            contenedor["alias"] = sorted(
                (set(contenedor.get("alias", [])) | set(entrada.get("alias", [])) | {termino})
                - {contenedor["termino"]}
            )
            indices_unidos = set(contenedor.get("pasadas_indices", [])) | set(
                entrada.get("pasadas_indices", [])
            )
            contenedor["pasadas_indices"] = sorted(indices_unidos)
            contenedor["pasadas_que_lo_mencionan"] = len(indices_unidos)
        else:
            fusionados.append(entrada)

    return fusionados


def parsear_args(args: list[str]) -> tuple[int, list[str]]:
    n = 3
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
            "Uso: uv run python src/calibrar_glosario.py [--n=3] "
            "<fragmento1.txt> [fragmento2.txt ...]"
        )
        sys.exit(1)

    n, rutas = parsear_args(sys.argv[1:])
    if not rutas:
        print("No se ha indicado ningún fragmento.")
        sys.exit(1)

    resultados = calibrar_n_veces(rutas, n)

    for i, resultado in enumerate(resultados, start=1):
        guardar(resultado, Path(f"data/exports/calibracion_pasada_{i}.json"))

    estables, discrepancias = reconciliar(resultados)
    personajes, lugares, glosario = repartir({"glosario": list(estables.values())})
    personajes = fusionar_alias(personajes, permitir_substring=True)
    lugares = fusionar_alias(lugares, permitir_substring=False)
    glosario = fusionar_alias(glosario, permitir_substring=False)

    print()
    guardar(personajes, Path("data/exports/personajes_obra.json"))
    guardar(lugares, Path("data/exports/lugares_obra.json"))
    guardar(glosario, Path("data/exports/glosario_obra.json"))
    guardar(discrepancias, Path("data/exports/discrepancias_calibracion.json"))

    print(
        f"\n{len(discrepancias)} término(s) con categoría inestable entre "
        "pasadas — revisar discrepancias_calibracion.json"
    )


if __name__ == "__main__":
    main()
