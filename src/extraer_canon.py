"""
extraer_canon.py

Fase 1 del bootstrapping: enviar un fragmento de la obra a Claude y
extraer canon estructurado (personajes, lugares, objetos, hechos),
incluyendo el momento_canonico y la atribución de cada hecho desde el
primer intento.

YA NO USA listas fijas inventadas a mano. Antes de ejecutar este script hay
que haber ejecutado la Fase 0:

    Fase 0a -> calibrar_taxonomia.py -> data/exports/tipos_documentales_obra.json
    Fase 0b -> calibrar_glosario.py  -> data/exports/personajes_obra.json
                                         data/exports/lugares_obra.json
                                         data/exports/glosario_obra.json

extraer_canon.py carga esos cuatro ficheros como CONFIG DE LA OBRA (no son
agentes, son el resultado ya reconciliado de la calibración previa) y se
los pasa al modelo en el propio prompt de sistema:

- La taxonomía de tipos documentales sustituye a la lista fija que este
  script usaba antes ("narrativa, enciclopedia, transcripcion, archivo,
  dialogo"). Esa lista era genérica y no reflejaba lo que la propia obra
  exhibe (p.ej. "Encabezado estructural de canto", "Discurso directo de
  personaje"...).
- El glosario calibrado (personajes + lugares + resto) le da al modelo la
  forma CANÓNICA de cada término ya visto en fragmentos previos, para que
  no vuelva a segmentar dos variantes del mismo nombre (p.ej. "Agamenón"
  y "Atrida Agamenón") como si fueran dos entidades distintas en cada
  fragmento nuevo.

Si alguno de los cuatro ficheros no existe todavía (no se ha ejecutado esa
fase), el script sigue funcionando: avisa por consola y el modelo extrae
ese fragmento sin la ayuda de esa calibración, igual que hacía antes.

ATRIBUCIÓN — el motivo de fondo de este campo: en una extracción anterior,
la jactancia de un personaje sobre un hecho no verificado se reprodujo
como hecho objetivo, indistinguible de algo afirmado por el narrador o
por un documento certificado. Si esa mezcla llega tal cual al canon
estructurado, cualquier reconciliación posterior entre fragmentos puede
acabar tratando una mentira o una fanfarronada de un personaje como canon
verificado. Por eso cada hecho_canonico ahora declara también quién
respalda esa afirmación: narrador/documento, o un personaje concreto sin
verificación independiente.

CACHÉ DE PROMPT — el bloque de sistema (reglas fijas + taxonomía de tipos
documentales + glosario calibrado) es IDÉNTICO entre fragmentos mientras no
se vuelva a ejecutar la Fase 0: lo único que cambia de una llamada a otra es
el fragmento del usuario. Eso lo convierte en el caso de libro para
cache_control: se marca como "ephemeral" en system, así que la primera
llamada de la sesión escribe el caché (paga 1.25x esa parte) y cada llamada
siguiente dentro de los 5 minutos lo lee (paga solo 0.1x esa parte) en vez
de reprocesar entero el glosario y la taxonomía en cada fragmento nuevo.
Solo entra en juego si ese bloque supera el mínimo cacheable de Sonnet
(1024 tokens) — con la Fase 0 ya ejecutada y sus ~12k caracteres lo supera
de sobra; sin Fase 0 (solo PROMPT_SISTEMA_BASE) probablemente no llegue, y
no pasa nada: simplemente no se cachea esa llamada.

No depende todavía de SQLite ni de chunking por escena.
Solo: fragmento + config de la obra -> Claude -> JSON.

Uso:
    uv run python src/extraer_canon.py data/novelas/fragmento.txt
    uv run python src/extraer_canon.py --config=otro/directorio data/novelas/fragmento.txt
"""

import json
import sys
from pathlib import Path

import anthropic

MODELO = "claude-sonnet-4-6"

DIRECTORIO_CONFIG_DEFAULT = "data/exports"

PROMPT_SISTEMA_BASE = """Eres un asistente de extracción de canon narrativo. \
Tu trabajo es leer un fragmento de una novela y extraer entidades y hechos \
de forma estructurada, SIN interpretar ni añadir nada que no esté en el texto.

Reglas importantes:
- No inventes nada. Si un dato no aparece explícito o claramente implícito \
en el fragmento, no lo incluyas.
- Para cada hecho_canonico, indica en "momento_canonico" en qué punto del \
fragmento se revela o establece ese hecho (usa una referencia textual breve, \
como "al inicio del fragmento" o "tras el diálogo entre los dos personajes"; \
NO inventes números de capítulo si no aparecen en el texto).
- Para cada hecho_canonico, indica también "atribucion": quién respalda \
que ese hecho sea verdadero.
  - "tipo": "narrador_documental" si lo establece el narrador externo o un \
documento citado dentro del texto (carta, informe, entrada de diario) como \
verdad dentro de su propio marco — es decir, nadie dentro de la ficción lo \
está poniendo en duda en ese fragmento.
  - "tipo": "afirmacion_personaje" si el hecho es lo que un personaje DICE, \
jacta, alega o cree, sin que el narrador ni ningún documento lo confirmen \
de forma independiente. Esto incluye jactancias, excusas, versiones \
interesadas y mentiras: extrae lo que el personaje afirma, pero etiquétalo \
como afirmación suya, NUNCA como hecho objetivo.
  - Si "tipo" es "afirmacion_personaje", indica en "personaje" el nombre \
de quien lo afirma. Si "tipo" es "narrador_documental", "personaje" va \
como null.
  - Ante la duda entre los dos, elige "afirmacion_personaje": es preferible \
marcar de más que dejar pasar una jactancia como canon objetivo.
- "tipo_documental" describe qué clase de texto es el fragmento en sí.
- Devuelve EXCLUSIVAMENTE el JSON, sin texto antes ni después, sin \
markdown ni bloques de código.
"""

PROMPT_USUARIO = """Extrae el canon de este fragmento siguiendo exactamente \
este esquema JSON:

{{
  "tipo_documental": "",
  "personajes": [],
  "lugares": [],
  "organizaciones": [],
  "objetos": [],
  "hechos_canonicos": [
    {{
      "hecho": "",
      "momento_canonico": "",
      "atribucion": {{"tipo": "", "personaje": null}}
    }}
  ]
}}

Fragmento:
---
{fragmento}
---
"""


def leer_fragmento(ruta: str) -> str:
    return Path(ruta).read_text(encoding="utf-8")


def cargar_config_obra(directorio: str = DIRECTORIO_CONFIG_DEFAULT) -> dict:
    """Carga los cuatro ficheros que produce la Fase 0 (calibrar_taxonomia.py
    y calibrar_glosario.py) y que este script usa como CONFIG de la obra,
    no como agentes ni como golden set: son el resultado ya reconciliado
    de fragmentos previos de ESTA novela.

    Si un fichero no existe (esa fase aún no se ha ejecutado sobre esta
    obra), se avisa por consola y se sigue con una lista vacía para esa
    parte de la config — el extractor cae de vuelta a su propio juicio
    para lo que esa calibración le habría resuelto, igual que se
    comportaba este script antes de la Fase 0."""
    base = Path(directorio)

    def cargar(nombre: str, valor_por_defecto):
        ruta = base / nombre
        if not ruta.exists():
            print(f"  Aviso: no se encontró {ruta}; sigo sin esa calibración.")
            return valor_por_defecto
        return json.loads(ruta.read_text(encoding="utf-8"))

    tipos_documentales = cargar(
        "tipos_documentales_obra.json", {"tipos_documentales": []}
    ).get("tipos_documentales", [])
    personajes = cargar("personajes_obra.json", [])
    lugares = cargar("lugares_obra.json", [])
    glosario = cargar("glosario_obra.json", [])

    return {
        "tipos_documentales": tipos_documentales,
        "personajes": personajes,
        "lugares": lugares,
        "glosario": glosario,
    }


def formatear_tipos_documentales(tipos: list[dict]) -> str:
    """Construye el bloque de prompt con la taxonomía de tipos documentales
    de ESTA obra (Fase 0a), para que "tipo_documental" se rellene con un
    nombre que la propia novela ya reveló, no con una categoría genérica
    inventada a mano."""
    if not tipos:
        return ""
    lineas = [f'- "{t["nombre"]}": {t["descripcion"]}' for t in tipos]
    return (
        "TIPOS DOCUMENTALES DE ESTA OBRA (calibrados en la Fase 0a sobre "
        "fragmentos previos). Usa "
        '"tipo_documental" con uno de estos nombres TAL CUAL aparecen aquí. '
        "Si el fragmento no encaja claramente en ninguno, usa el más "
        "parecido y no inventes una categoría genérica nueva:\n"
        + "\n".join(lineas)
    )


def formatear_glosario(personajes: list[dict], lugares: list[dict], glosario: list[dict]) -> str:
    """Construye el bloque de prompt con el glosario calibrado de la obra
    (Fase 0b): personajes, lugares y el resto de términos ya identificados
    y reconciliados en fragmentos previos, con su forma canónica y sus
    variantes conocidas."""
    entradas = personajes + lugares + glosario
    if not entradas:
        return ""
    lineas = []
    for entrada in entradas:
        variantes = entrada.get("variantes") or []
        sufijo = f" (variantes ya vistas: {', '.join(variantes)})" if variantes else ""
        lineas.append(f'- {entrada["termino"]} [{entrada["categoria"]}]{sufijo}')
    return (
        "GLOSARIO CALIBRADO DE ESTA OBRA (Fase 0b, sobre fragmentos previos). "
        "Si mencionas alguno de estos términos en personajes, lugares u "
        "organizaciones, usa SIEMPRE la forma canónica indicada aquí (no una "
        "variante), para que el mismo referente no acabe con nombres "
        "distintos según el fragmento. Si el fragmento revela un término "
        "que no está en esta lista, extráelo de todos modos con normalidad:\n"
        + "\n".join(lineas)
    )


def construir_prompt_sistema(config: dict) -> str:
    partes = [PROMPT_SISTEMA_BASE]

    tipos_txt = formatear_tipos_documentales(config["tipos_documentales"])
    if tipos_txt:
        partes.append(tipos_txt)

    glosario_txt = formatear_glosario(
        config["personajes"], config["lugares"], config["glosario"]
    )
    if glosario_txt:
        partes.append(glosario_txt)

    return "\n\n".join(partes)


def mostrar_uso_cache(uso) -> None:
    """Imprime el desglose de tokens de la llamada para poder confirmar en
    marcha si el bloque de sistema (taxonomía + glosario) está entrando
    por caché. cache_creation_input_tokens > 0 = esta llamada ESCRIBIÓ el
    caché (1.25x esa parte; normal en la primera llamada de la sesión).
    cache_read_input_tokens > 0 = esta llamada LEYÓ un caché ya existente
    (0.1x esa parte; lo esperado a partir de la segunda llamada dentro de
    los 5 minutos). Si ambos salen a 0 con la Fase 0 ya cargada, algo no
    está coincidiendo byte a byte entre llamadas (revisar que config no
    haya cambiado a mitad de sesión)."""
    creacion = getattr(uso, "cache_creation_input_tokens", 0) or 0
    lectura = getattr(uso, "cache_read_input_tokens", 0) or 0
    print(
        f"  Tokens — nuevos: {uso.input_tokens} | "
        f"caché escrito: {creacion} | caché leído: {lectura} | "
        f"salida: {uso.output_tokens}"
    )


def extraer_canon(fragmento: str, config: dict) -> dict:
    cliente = anthropic.Anthropic()
    prompt_sistema = construir_prompt_sistema(config)

    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=3000,
        system=[
            {
                "type": "text",
                "text": prompt_sistema,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": PROMPT_USUARIO.format(fragmento=fragmento),
            }
        ],
    )

    mostrar_uso_cache(respuesta.usage)

    texto = respuesta.content[0].text.strip()

    # por si el modelo se cuela y envuelve en ```json ... ```
    texto = texto.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        return json.loads(texto)
    except json.JSONDecodeError as e:
        print(f"\n--- ERROR AL PARSEAR JSON: {e} ---")
        print(f"stop_reason: {respuesta.stop_reason}")
        print(texto)
        raise


def parsear_args(args: list[str]) -> tuple[str, str]:
    directorio_config = DIRECTORIO_CONFIG_DEFAULT
    ruta_fragmento = None
    for arg in args:
        if arg.startswith("--config="):
            directorio_config = arg.split("=", 1)[1]
        else:
            ruta_fragmento = arg
    return directorio_config, ruta_fragmento


def main():
    if len(sys.argv) < 2:
        print(
            "Uso: uv run python src/extraer_canon.py [--config=data/exports] "
            "<ruta_fragmento.txt>"
        )
        sys.exit(1)

    directorio_config, ruta_entrada = parsear_args(sys.argv[1:])
    if not ruta_entrada:
        print("No se ha indicado ningún fragmento.")
        sys.exit(1)

    fragmento = leer_fragmento(ruta_entrada)
    print(f"Fragmento leído: {len(fragmento)} caracteres")

    print(f"Cargando config de la obra desde {directorio_config}...")
    config = cargar_config_obra(directorio_config)
    print(
        f"  {len(config['tipos_documentales'])} tipos documentales, "
        f"{len(config['personajes'])} personajes, "
        f"{len(config['lugares'])} lugares, "
        f"{len(config['glosario'])} términos de glosario."
    )

    print("Llamando a Claude...")
    canon = extraer_canon(fragmento, config)

    print(json.dumps(canon, indent=2, ensure_ascii=False))

    ruta_salida = Path("data/exports/canon_fragmento.json")
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    ruta_salida.write_text(
        json.dumps(canon, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\nGuardado en {ruta_salida}")


if __name__ == "__main__":
    main()
