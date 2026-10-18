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
semántico del propio modelo — para consolidar entradas. Solo en
personajes se añade además una subcadena de respaldo (nombre corto
contenido en nombre completo): en otras categorías una subcadena suele
señalar una entidad relacionada pero DISTINTA (p.ej. un lugar y una
institución que toma su nombre de ese lugar pero es un referente
distinto), así que ahí no se activa.

IMPORTANTE: no se le da al modelo ningún glosario humano preexistente.
Tiene que inferir la categoría únicamente de cómo se usa el término en el
propio texto (¿recibe trato personal? ¿aparece en fórmulas fijas? ¿alguna
nota a pie de página lo define?).

SALIDA: el resultado reconciliado se reparte en CUATRO ficheros, todos bajo
data/<obra>/exports/:
    personajes_obra.json         (categoria == persona, con alias fusionados)
    lugares_obra.json            (categoria == lugar)
    glosario_obra.json           (objeto, organizacion, pueblo,
                                   concepto_abstracto, incierto)
    discrepancias_calibracion.json (términos en empate, sin mayoría clara
                                   entre pasadas)
Además guarda cada pasada en bruto en calibracion_pasada_N.json (misma
carpeta) por si hace falta auditar qué dijo cada una.

MODO INCREMENTAL (por defecto si la obra ya tiene glosario): recalibrar
TODA la obra cada vez que llega un fragmento no escala — cada pasada
devuelve un número de términos casi fijo (~140-145) sea cual sea el
corpus, así que al crecer la obra se pierden términos en silencio, y
además cambian formas canónicas que el canon ya extraído usa. En modo
incremental solo se envían los fragmentos NUEVOS, junto con el glosario
ya fijado, y se le pide al modelo que devuelva solo lo nuevo (términos
nuevos, o formas nuevas de términos ya fijados). incorporar() lo añade
sin tocar ningún término canónico ni categoría existentes: lo que choque
con ellos va a discrepancias. Las pasadas en bruto se guardan como
calibracion_<fragmentos>_pasada_N.json para no pisar las anteriores.
--desde-cero fuerza la recalibración completa (y reescribe el glosario).

--obra=<nombre> es OBLIGATORIO: cada obra calibra por separado y todas sus
salidas quedan bajo data/<obra>/exports/, para que el glosario de una
novela nunca se mezcle con el de otra.

Uso:
    uv run python src/calibrar_glosario.py --obra=nombre_obra [--n=3] [--desde-cero] data/novelas/fragmento_1.txt data/novelas/fragmento_2.txt ...
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
texto (p.ej. un nombre de persona solo vs. con su patronímico, o el \
nombre corto de un lugar vs. su denominación completa), usa como "termino" \
la forma más completa que hayas visto y lista las OTRAS formas observadas \
en "variantes" — sin repetir ahí la que ya pusiste en "termino". No \
incluyas entidades relacionadas pero DISTINTAS (p.ej. no pongas un lugar \
como variante de una institución que opera allí: son dos referentes \
distintos, no dos nombres del mismo). Si solo hay una forma observada, \
deja "variantes" vacía.

VARIANTES FRENTE A EPÍTETOS: en "variantes" pon SOLO formas que nombran a \
este referente y a ningún otro (su nombre, su nombre completo, un apellido o \
patronímico que en esta obra solo lleva él). Las fórmulas descriptivas que \
podrían aplicarse a otros personajes o entidades (un oficio, una cualidad, \
un rasgo físico, un parentesco genérico; p.ej. "el de los pies ligeros", \
"hijo del rey") van en "epitetos", aunque en este texto solo se usen para \
él. En caso de duda, a "epitetos". No repitas una forma en los dos campos.

Devuelve EXCLUSIVAMENTE un JSON con esta forma, sin texto antes ni después, \
sin markdown:

{
  "glosario": [
    {
      "termino": "",
      "categoria": "",
      "variantes": [],
      "epitetos": [],
      "evidencia": "breve cita o descripción del patrón de uso observado",
      "confianza": "alta | media | baja"
    }
  ]
}
"""


PROMPT_INCREMENTAL = """GLOSARIO YA FIJADO DE ESTA OBRA (calibrado sobre \
fragmentos anteriores). Estos términos ya tienen forma canónica y categoría \
definitivas; los fragmentos que recibes ahora son NUEVOS. En tu respuesta:
- NO devuelvas un término ya fijado, salvo que en estos fragmentos aparezca \
con alguna forma que no esté ya en su lista. En ese caso devuélvelo con \
"termino" EXACTAMENTE igual a la forma canónica de esta lista y pon en \
"variantes" solo las formas nuevas (y en "epitetos", los epítetos nuevos).
- Un nombre que ya figura como variante de un término fijado no es un \
término nuevo: no lo devuelvas.
- Que un término nuevo comparta un epíteto con uno fijado no los convierte \
en el mismo referente.
- Todo lo demás que cumpla los criterios de arriba, devuélvelo como término \
nuevo con normalidad. No hace falta que se repita en varios fragmentos: \
basta con que se repita dentro de estos.
- Clasifica cada término nuevo por cómo se usa en estos fragmentos; si la \
evidencia contradice la categoría de un término ya fijado, devuélvelo con la \
categoría que observas y explícalo en "evidencia".

"""


def leer_fragmento(ruta: str) -> str:
    return Path(ruta).read_text(encoding="utf-8")


def construir_entrada(rutas: list[str]) -> str:
    partes = []
    for i, ruta in enumerate(rutas, start=1):
        texto = leer_fragmento(ruta)
        partes.append(f"=== FRAGMENTO {i} ({Path(ruta).name}) ===\n{texto}")
    return "\n\n".join(partes)


def mostrar_uso_cache(uso) -> None:
    """Ver extraer_canon.py: mismo propósito, mismo criterio de lectura.
    Aquí el bloque cacheado es la ENTRADA (fragmentos), no el sistema —
    calibrar_n_veces() manda el mismo texto N veces en la misma ejecución,
    así que la pasada 1 escribe el caché y las pasadas 2..N deberían leerlo."""
    creacion = getattr(uso, "cache_creation_input_tokens", 0) or 0
    lectura = getattr(uso, "cache_read_input_tokens", 0) or 0
    print(
        f"  Tokens — nuevos: {uso.input_tokens} | "
        f"caché escrito: {creacion} | caché leído: {lectura} | "
        f"salida: {uso.output_tokens}"
    )


def formatear_registros(registros: dict[str, list[dict]]) -> str:
    lineas = []
    for entradas in registros.values():
        for e in entradas:
            formas = sorted(set(e.get("variantes") or []) | set(e.get("alias") or []))
            sufijo = f" (variantes: {', '.join(formas)})" if formas else ""
            if e.get("epitetos"):
                sufijo += f" (epítetos: {', '.join(e['epitetos'])})"
            lineas.append(f'- {e["termino"]} [{e["categoria"]}]{sufijo}')
    return PROMPT_INCREMENTAL + "\n".join(lineas)


def construir_prompt_sistema(registros: dict[str, list[dict]] | None = None) -> str:
    """Sin glosario previo, el prompt de siempre. Con él, se añade el
    glosario fijado y las reglas del modo incremental."""
    if not registros:
        return PROMPT_SISTEMA
    return PROMPT_SISTEMA + "\n" + formatear_registros(registros)


def calibrar(rutas: list[str], registros: dict[str, list[dict]] | None = None) -> dict:
    cliente = anthropic.Anthropic()
    entrada = construir_entrada(rutas)

    print(f"Enviando {len(rutas)} fragmentos ({len(entrada)} caracteres totales)...")

    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=16000,
        system=construir_prompt_sistema(registros),
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": entrada,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
    )

    mostrar_uso_cache(respuesta.usage)

    texto = respuesta.content[0].text.strip()
    texto = texto.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        return json.loads(texto)
    except json.JSONDecodeError as e:
        print(f"\n--- ERROR AL PARSEAR JSON: {e} ---")
        print(f"stop_reason: {respuesta.stop_reason}")
        print(texto)
        raise


def calibrar_n_veces(
    rutas: list[str], n: int, registros: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """Ejecuta calibrar() n veces de forma independiente sobre el mismo lote.
    Si una pasada falla (p.ej. JSON truncado), se descarta esa pasada sin
    perder las que ya se completaron — cada pasada es una llamada de pago,
    no tiene sentido tirarlas todas por un fallo aislado."""
    resultados = []
    for i in range(n):
        print(f"\n--- Pasada {i + 1}/{n} ---")
        try:
            resultados.append(calibrar(rutas, registros))
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
        entrada_elegida = max(
            candidatas, key=lambda e: ORDEN_CONFIANZA.get(e.get("confianza"), 0)
        )
        disenso = [
            {"categoria": e.get("categoria"), "confianza": e.get("confianza")}
            for e in entradas
            if e.get("categoria") != categoria_mayoritaria
        ]
        # Las respuestas anteriores al campo "epitetos" no lo traen: [].
        # Si una pasada da una forma como variante y otra como epíteto,
        # gana epíteto: tratar como nombre propio algo que no lo es cuesta
        # más (atribuye hechos ajenos) que perder una forma de búsqueda.
        # Sin distinguir mayúsculas, como la búsqueda de hechos (ILIKE).
        epitetos_unidos = sorted(
            {v for e in entradas for v in (e.get("epitetos") or []) if v and v != termino}
        )
        es_epiteto = {v.casefold() for v in epitetos_unidos}
        variantes_unidas = sorted(
            {v for e in entradas for v in (e.get("variantes") or [])
             if v and v != termino and v.casefold() not in es_epiteto}
        )

        estables[termino] = {
            **entrada_elegida,
            "categoria": categoria_mayoritaria,
            "variantes": variantes_unidas,
            "epitetos": epitetos_unidos,
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
    modelo sobre qué cuenta como "el mismo referente" (p.ej. el nombre
    corto de una organización y su denominación completa, nombrados por
    separado en el texto).

    Señal de respaldo (permitir_substring=True): si el término de una
    entrada aparece como palabra completa dentro del término de otra, se
    fusionan. Solo se activa para personajes por defecto — en otras
    categorías una subcadena suele señalar una entidad relacionada pero
    DISTINTA (p.ej. un lugar y una institución que toma su nombre de ese
    lugar pero es un referente distinto), así que ahí basta con las
    variantes autoreportadas."""
    pendientes = []
    for e in entradas:
        e = dict(e)
        # Un epíteto nunca es alias, tampoco con otras mayúsculas.
        es_epiteto = {v.casefold() for v in e.get("epitetos") or []}
        propias = {v for v in (e.get("variantes") or [])
                   if v and v != e["termino"] and v.casefold() not in es_epiteto}
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
            if entrada.get("epitetos"):
                contenedor["epitetos"] = sorted(
                    set(contenedor.get("epitetos") or []) | set(entrada["epitetos"]))
        else:
            fusionados.append(entrada)

    # Un alias nunca puede ser otra entidad del registro: al unir alias de
    # varias entradas se podía colar el término de una entidad distinta.
    terminos = {f["termino"] for f in fusionados}
    for f in fusionados:
        f["alias"] = sorted(set(f["alias"]) - terminos)

    return fusionados


def separar_ambiguos(entradas: list[dict]) -> tuple[list[dict], list[dict]]:
    """Aparta las entradas "paraguas": las que dan como variantes a dos o
    más entidades distintas del mismo registro (p.ej. un "Ayax" con
    variantes "Ayax Telamonio" y "Ayax de Oileo"). Fusionarla con
    cualquiera de ellas confundiría a las dos, así que no entra en el
    glosario y se devuelve como discrepancia para revisar a mano. Si otras
    pasadas dieron ese nombre como variante de una sola entidad, ahí sigue."""
    terminos = {e["termino"] for e in entradas}
    limpias, ambiguas = [], []
    for e in entradas:
        nombradas = sorted({v for v in e.get("variantes") or []
                            if v in terminos and v != e["termino"]})
        if len(nombradas) < 2:
            limpias.append(e)
            continue
        ambiguas.append({
            "termino": e["termino"],
            "pasadas_totales": e.get("pasadas_totales"),
            "pasadas_que_lo_mencionan": e.get("pasadas_que_lo_mencionan"),
            "motivo": "término ambiguo: sus variantes son entidades distintas",
            "entidades": nombradas,
        })
    return limpias, ambiguas


REGISTROS = {
    "personajes": "personajes_obra.json",
    "lugares": "lugares_obra.json",
    "glosario": "glosario_obra.json",
}


MOTIVO_COMPARTIDA = "forma compartida"


def formas_vetadas(discrepancias: list[dict]) -> set[str]:
    """Formas ya declaradas compartidas en calibraciones anteriores: no
    pueden volver a ser alias de nadie (si no, la primera entidad que las
    reclamara en la siguiente calibración se quedaría con ellas)."""
    return {d["termino"] for d in discrepancias if d.get("motivo") == MOTIVO_COMPARTIDA}


def retirar_formas_compartidas(
    registros: dict[str, list[dict]], vetadas: set[str] = frozenset(),
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Red de seguridad determinista: el modelo no siempre separa bien
    variantes y epítetos. Toda forma asociada a dos o más entidades, en
    cualquiera de los registros (como término, alias o variante), sale de
    los alias y variantes de todas ellas y va a discrepancias. Cubre los
    epítetos genéricos que el modelo dio como variante y los patronímicos
    ambiguos ("Atrida": Agamenón y Menelao). El término canónico de una
    entidad nunca se le quita. Los epítetos no cuentan: compartirlos es
    lo normal. Se compara sin distinguir mayúsculas, como la búsqueda."""
    duenos: dict[str, set[str]] = {}
    for entradas in registros.values():
        for e in entradas:
            for forma in {e["termino"], *(e.get("alias") or []), *(e.get("variantes") or [])}:
                duenos.setdefault(forma.casefold(), set()).add(e["termino"])
    compartidas = {f for f, d in duenos.items() if len(d) > 1}
    retirar = compartidas | {v.casefold() for v in vetadas}

    nuevos = {}
    for nombre, entradas in registros.items():
        nuevos[nombre] = []
        for e in entradas:
            e = dict(e)
            for campo in ("alias", "variantes"):
                if campo in e:
                    e[campo] = [f for f in e[campo] or []
                                if f.casefold() not in retirar or f == e["termino"]]
            nuevos[nombre].append(e)

    originales = {}  # forma tal como se escribió la primera vez
    for entradas in registros.values():
        for e in entradas:
            for forma in [e["termino"], *(e.get("alias") or []), *(e.get("variantes") or [])]:
                originales.setdefault(forma.casefold(), forma)
    discrepancias = [
        {"termino": originales[f], "motivo": MOTIVO_COMPARTIDA, "entidades": sorted(duenos[f])}
        for f in sorted(compartidas)
    ]
    return nuevos, discrepancias


def candidatos_a_epiteto(registros: dict[str, list[dict]]) -> list[dict]:
    """Alias que parecen fórmulas descriptivas y no nombres: empiezan por
    minúscula y no contienen ninguna palabra en mayúscula ("domador de
    caballos", pero no "vástago de Marte"). Es una heurística del
    castellano (también caen gentilicios en minúscula), así que solo
    produce una lista para revisar a mano: no se mueve nada."""
    candidatos = []
    for nombre, entradas in registros.items():
        for e in entradas:
            for forma in sorted(set(e.get("alias") or []) | set(e.get("variantes") or [])):
                if forma[:1].islower() and not any(p[:1].isupper() for p in forma.split()):
                    candidatos.append({"termino": e["termino"], "registro": nombre,
                                       "forma": forma})
    return candidatos


def mover_a_epitetos(
    registros: dict[str, list[dict]], confirmados: list[dict],
) -> dict[str, list[dict]]:
    """Pasa a "epitetos" las formas confirmadas a mano (entradas
    {"termino", "forma"}, el mismo formato que candidatos_a_epiteto)."""
    por_termino: dict[str, set[str]] = {}
    for c in confirmados:
        por_termino.setdefault(c["termino"], set()).add(c["forma"])
    nuevos = {}
    for nombre, entradas in registros.items():
        nuevos[nombre] = []
        for e in entradas:
            formas = por_termino.get(e["termino"], set())
            if formas:
                e = dict(e)
                movidas = formas & (set(e.get("alias") or []) | set(e.get("variantes") or []))
                for campo in ("alias", "variantes"):
                    if campo in e:
                        e[campo] = [f for f in e[campo] or [] if f not in formas]
                e["epitetos"] = sorted(set(e.get("epitetos") or []) | movidas)
            nuevos[nombre].append(e)
    return nuevos


def _repartir_y_fusionar(
    estables: dict[str, dict],
) -> tuple[dict[str, list[dict]], list[dict]]:
    personajes, lugares, glosario = repartir({"glosario": list(estables.values())})
    registros, ambiguas = {}, []
    for nombre, entradas, substring in (
        ("personajes", personajes, True),
        ("lugares", lugares, False),
        ("glosario", glosario, False),
    ):
        entradas, apartadas = separar_ambiguos(entradas)
        ambiguas += apartadas
        registros[nombre] = fusionar_alias(entradas, permitir_substring=substring)
    return registros, ambiguas


def construir_registros(
    estables: dict[str, dict], vetadas: set[str] = frozenset(),
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Reparte los términos reconciliados en personajes/lugares/glosario,
    aparta los ambiguos, fusiona alias dentro de cada registro y retira
    las formas compartidas entre entidades."""
    registros, ambiguas = _repartir_y_fusionar(estables)
    registros, compartidas = retirar_formas_compartidas(registros, vetadas)
    return registros, ambiguas + compartidas


def incorporar(
    existentes: dict[str, list[dict]], estables: dict[str, dict],
    vetadas: set[str] = frozenset(),
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Añade al glosario ya fijado lo que trae una calibración incremental.

    Garantías (ver tests/SPECS.md):
      - ningún término existente desaparece ni cambia de forma canónica o
        de categoría;
      - una forma nueva de un término existente se le añade como variante;
      - una forma que acaba en dos entidades no se queda en ninguna: va a
        discrepancias como forma compartida (retirar_formas_compartidas),
        en vez de quedársela la primera que la reclamó;
      - los epítetos se guardan, pero no identifican a ninguna entidad;
      - lo que choca con lo fijado (otra categoría, un nombre que podría
        ser de varias entidades) va a discrepancias, no al glosario.
    """
    registros = {k: [dict(e) for e in v] for k, v in existentes.items()}
    por_termino = {e["termino"]: e for entradas in registros.values() for e in entradas}
    duenos: dict[str, set[str]] = {}  # forma -> términos fijados que la usan
    for e in por_termino.values():
        for forma in [e["termino"], *(e.get("variantes") or []), *(e.get("alias") or [])]:
            duenos.setdefault(forma, set()).add(e["termino"])

    discrepancias: list[dict] = []
    nuevos: dict[str, dict] = {}
    # Primero lo que es de entidades ya fijadas: así sus formas nuevas ya
    # constan cuando otra pasada da una de ellas como término suelto (una
    # pasada dio "Minerva alalcomenia" como variante de Minerva y otra como
    # término propio; procesada antes, acababa como entidad aparte).
    def es_de_fijada(par):
        termino, entrada = par
        return termino in por_termino or any(
            v in por_termino for v in entrada.get("variantes") or [])

    for termino, entrada in sorted(estables.items(), key=lambda par: not es_de_fijada(par)):
        formas = [termino, *(entrada.get("variantes") or [])]
        # Es una entidad ya fijada si su nombre ya es una forma conocida, o
        # si el modelo da como variante el término canónico de una. Compartir
        # solo un epíteto ("la de los grandes ojos") NO la identifica: el
        # mismo epíteto se aplica a personajes distintos.
        if termino in por_termino:
            candidatos = {termino}
        elif termino in duenos:
            candidatos = set(duenos[termino])
        else:
            candidatos = set()
        candidatos |= {v for v in formas[1:] if v in por_termino}
        if not candidatos:
            # Entidad nueva. Si alguna de sus formas ya es de otra entidad,
            # la conserva: retirar_formas_compartidas la quitará de las dos.
            nuevos[termino] = entrada
            continue
        if len(candidatos) > 1:
            discrepancias.append({
                "termino": termino,
                "motivo": "término ambiguo: sus formas pertenecen a varias entidades ya fijadas",
                "entidades": sorted(candidatos),
            })
            continue

        fijado = por_termino[candidatos.pop()]
        if entrada.get("categoria") != fijado["categoria"]:
            discrepancias.append({
                "termino": fijado["termino"],
                "motivo": "categoría distinta de la ya fijada",
                "categoria_fijada": fijado["categoria"],
                "categoria_propuesta": entrada.get("categoria"),
                "evidencia": entrada.get("evidencia"),
            })
        es_epiteto = {v.casefold() for v in [*(fijado.get("epitetos") or []),
                                             *(entrada.get("epitetos") or [])]}
        formas_nuevas = {f for f in formas
                         if f != fijado["termino"] and fijado["termino"] not in duenos.get(f, ())
                         and f.casefold() not in es_epiteto}
        if formas_nuevas:
            for campo in ("variantes", "alias"):
                fijado[campo] = sorted(set(fijado.get(campo) or []) | formas_nuevas)
            for f in formas_nuevas:
                duenos.setdefault(f, set()).add(fijado["termino"])
        epitetos_nuevos = set(entrada.get("epitetos") or []) - set(fijado.get("epitetos") or [])
        if epitetos_nuevos:
            fijado["epitetos"] = sorted(set(fijado.get("epitetos") or []) | epitetos_nuevos)

    nuevos_registros, ambiguas = _repartir_y_fusionar(nuevos)
    for nombre, entradas in nuevos_registros.items():
        registros.setdefault(nombre, []).extend(entradas)
    registros, compartidas = retirar_formas_compartidas(registros, vetadas)
    return registros, discrepancias + ambiguas + compartidas


def cargar_registros(directorio: Path) -> dict[str, list[dict]] | None:
    """El glosario ya fijado de la obra, o None si aún no se ha calibrado."""
    if not all((directorio / f).exists() for f in REGISTROS.values()):
        return None
    return {k: json.loads((directorio / f).read_text(encoding="utf-8"))
            for k, f in REGISTROS.items()}


def parsear_args(args: list[str]) -> dict:
    opciones = {"obra": None, "n": 3, "desde_cero": False, "sanear": False,
                "epitetos": None, "rutas": []}
    for arg in args:
        if arg.startswith("--obra="):
            opciones["obra"] = arg.split("=", 1)[1]
        elif arg.startswith("--n="):
            opciones["n"] = int(arg.split("=", 1)[1])
        elif arg == "--desde-cero":
            opciones["desde_cero"] = True
        elif arg == "--sanear":
            opciones["sanear"] = True
        elif arg.startswith("--epitetos="):
            opciones["epitetos"] = arg.split("=", 1)[1]
        else:
            opciones["rutas"].append(arg)
    return opciones


def leer_discrepancias(ruta: Path) -> list[dict]:
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else []


def sanear(directorio: Path, ruta_epitetos: str | None) -> None:
    """Sanea un glosario ya fijado sin llamar a la API (y sin cambiar
    ningún término canónico):
      --sanear           retira las formas compartidas (a discrepancias) y
                         escribe candidatos_epiteto.json para revisarlo a mano;
      --epitetos=<json>  pasa a "epitetos" las formas de esa lista (la de
                         candidatos, tras quitar a mano las que no lo son).
    Después hay que recargar las entidades con poblar_almacen.py."""
    registros = cargar_registros(directorio)
    if registros is None:
        print(f"No hay glosario fijado en {directorio}.")
        sys.exit(1)
    ruta_discrepancias = directorio / "discrepancias_calibracion.json"
    anteriores = leer_discrepancias(ruta_discrepancias)

    if ruta_epitetos:
        confirmados = json.loads(Path(ruta_epitetos).read_text(encoding="utf-8"))
        registros = mover_a_epitetos(registros, confirmados)
        print(f"{len(confirmados)} forma(s) pasadas a epítetos.")
    registros, compartidas = retirar_formas_compartidas(registros, formas_vetadas(anteriores))
    for nombre, fichero in REGISTROS.items():
        guardar(registros[nombre], directorio / fichero)
    if compartidas:
        guardar(anteriores + [{**d, "lote": "saneado"} for d in compartidas], ruta_discrepancias)
    print(f"\n{len(compartidas)} forma(s) compartida(s) retiradas:")
    for d in compartidas:
        print(f"  {d['termino']}: {', '.join(d['entidades'])}")

    candidatos = candidatos_a_epiteto(registros)
    guardar(candidatos, directorio / "candidatos_epiteto.json")
    print(f"\n{len(candidatos)} candidato(s) a epíteto (revisar a mano; no se ha movido nada):")
    for c in candidatos:
        print(f"  {c['termino']} [{c['registro']}]: {c['forma']}")


def main():
    if len(sys.argv) < 2:
        print(
            "Uso: uv run python src/calibrar_glosario.py --obra=nombre_obra "
            "[--n=3] [--desde-cero] <fragmento1.txt> [fragmento2.txt ...]\n"
            "     uv run python src/calibrar_glosario.py --obra=nombre_obra "
            "--sanear [--epitetos=lista.json]"
        )
        sys.exit(1)

    opciones = parsear_args(sys.argv[1:])
    obra, n, desde_cero, rutas = (opciones[k] for k in ("obra", "n", "desde_cero", "rutas"))
    if not obra:
        print("Falta --obra=nombre_obra (evita mezclar salidas entre novelas).")
        sys.exit(1)
    directorio_salida = Path("data") / obra / "exports"
    if opciones["sanear"] or opciones["epitetos"]:
        sanear(directorio_salida, opciones["epitetos"])
        return
    if not rutas:
        print("No se ha indicado ningún fragmento.")
        sys.exit(1)

    existentes = None if desde_cero else cargar_registros(directorio_salida)

    if existentes is None:
        print("Calibración completa (sin glosario previo, o con --desde-cero).")
        prefijo = "calibracion"
    else:
        total = sum(len(v) for v in existentes.values())
        print(f"Calibración incremental sobre un glosario de {total} términos.")
        prefijo = "calibracion_" + "+".join(Path(r).stem for r in rutas)

    resultados = calibrar_n_veces(rutas, n, existentes)
    for i, resultado in enumerate(resultados, start=1):
        guardar(resultado, directorio_salida / f"{prefijo}_pasada_{i}.json")

    estables, discrepancias = reconciliar(resultados)
    ruta_discrepancias = directorio_salida / "discrepancias_calibracion.json"
    if existentes is None:
        registros, otras = construir_registros(estables)
        discrepancias += otras
    else:
        anteriores = leer_discrepancias(ruta_discrepancias)
        registros, otras = incorporar(existentes, estables, formas_vetadas(anteriores))
        lote = [Path(r).name for r in rutas]
        discrepancias = [{**d, "lote": lote} for d in discrepancias + otras]
        discrepancias = anteriores + discrepancias
        nuevos = {k: len(registros[k]) - len(existentes[k]) for k in REGISTROS}
        print(f"\nTérminos nuevos: {nuevos}")

    print()
    for nombre, fichero in REGISTROS.items():
        guardar(registros[nombre], directorio_salida / fichero)
    guardar(discrepancias, ruta_discrepancias)

    print(
        f"\n{len(discrepancias)} discrepancia(s) — revisar discrepancias_calibracion.json"
    )


if __name__ == "__main__":
    main()
