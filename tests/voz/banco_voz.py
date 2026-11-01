"""Banco de casos del agente de Voz y edición (tests/fijos/voz/casos.json).

Lo que comparten las pruebas del banco: construir cada caso sobre el texto
de su canto, el perfil de la obra (Cantos I-IV, de los textos fijos: el
agente no necesita la base de datos), puntuar el informe, clasificar cada
fallo léxico como del filtro o del agente, y escribir el informe.

El informe y las respuestas en bruto se escriben en NOVELA_SALIDA_VOZ (por
defecto .cache/voz/, fuera de git). Las respuestas grabadas de la suite
rápida se leen de tests/fijos/voz/respuestas/: para fijarlas, se copian
ahí a mano las de una ejecución revisada.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from voz import (GRAVEDADES, RASGOS, Aviso, Informe, Medida, Perfil, contiene_palabra,
                 normalizar, perfil_de_textos)

RAIZ = Path(__file__).resolve().parents[2]
CASOS = RAIZ / "tests" / "fijos" / "voz" / "casos.json"
GRABADAS = RAIZ / "tests" / "fijos" / "voz" / "respuestas"
TEXTOS = RAIZ / "tests" / "fijos" / "textos"
CANTOS = {1: "i", 2: "ii", 3: "iii", 4: "iv"}
SALIDA = Path(os.environ.get("NOVELA_SALIDA_VOZ", RAIZ / ".cache" / "voz"))

# Umbral de aceptación (docs/agente_voz.md, «Cómo se mide»).
MINIMO_LEXICO = 5
MAXIMO_AVISOS_EN_LIMPIOS = 1

SIN_AVISOS = {"control", "trampa"}


def cargar_casos() -> list[dict]:
    return json.loads(CASOS.read_text(encoding="utf-8"))["casos"]


def texto(orden: int) -> str:
    return (TEXTOS / f"iliada_canto_{CANTOS[orden]}.txt").read_text(encoding="utf-8")


def perfil_del_banco() -> Perfil:
    return perfil_de_textos([texto(o) for o in CANTOS])


def construir_caso(caso: dict, fragmento: str) -> tuple[str, int]:
    """(pasaje, char), como en el banco de Continuidad: con `tras_verso`, el
    pasaje es `texto` al final del párrafo de ese verso; con solo `verso`,
    el propio párrafo, en su sitio."""
    verso = caso.get("tras_verso", caso.get("verso"))
    m = re.search(rf"^{verso} .*$", fragmento, re.M)
    if not m:
        raise ValueError(f"{caso['id']}: no hay ninguna línea que empiece por «{verso} ».")
    if "tras_verso" in caso:
        return caso["texto"], m.end()
    return m.group(0), m.start()


def medida_desde_dict(datos: dict) -> Medida:
    return Medida(avisos_norma=[Aviso(**a) for a in datos["avisos_norma"]],
                  candidatos=list(datos["candidatos"]))


# --- Puntuación -------------------------------------------------------------

def formas_avisadas(avisos: list[Aviso]) -> set[str]:
    return {a.cita.lower() for a in avisos if a.clase == "norma"}


def puntuar(caso: dict, medida: Medida, informe: Informe) -> dict:
    """Norma: detectado si todas las formas esperadas tienen su aviso.
    Léxico: detectado si algún aviso léxico cita una de las palabras; si
    no, es fallo del filtro cuando ninguna llegó a los candidatos, y del
    agente cuando llegó. Controles y trampas: correcto sin avisos."""
    normas = [a for a in informe.avisos if a.clase == "norma"]
    lexicos = [a for a in informe.avisos if a.clase == "lexico"]
    resultado = {"avisos_norma": len(normas), "avisos_lexico": len(lexicos)}
    if caso["categoria"] in SIN_AVISOS:
        resultado["correcto"] = not informe.avisos
    elif caso["categoria"] == "norma":
        esperadas = {f.lower() for e in caso["esperado"] for f in e["formas"]}
        resultado["detectado"] = esperadas <= formas_avisadas(normas)
    else:
        [esperado] = caso["esperado"]
        palabras = {p.lower() for p in esperado["palabras"]}
        citados = [a for a in lexicos if a.palabra and a.palabra.lower() in palabras]
        resultado["detectado"] = bool(citados)
        if citados:
            resultado["gravedad_correcta"] = any(a.gravedad == esperado["gravedad"] for a in citados)
        elif palabras & {c.lower() for c in medida.candidatos}:
            resultado["fallo"] = "agente"
        else:
            resultado["fallo"] = "filtro"
    return resultado


def garantias_incumplidas(informe: Informe, medida: Medida, pasaje: str) -> list[str]:
    """Las garantías de «Validar», comprobadas sobre el informe final."""
    fallos = []
    candidatos = {c.lower() for c in medida.candidatos}
    for a in informe.avisos:
        if normalizar(a.cita) not in normalizar(pasaje):
            fallos.append(f"cita no literal: {a.cita!r}")
        if a.clase == "lexico":
            if not a.palabra or a.palabra.lower() not in candidatos \
                    or not contiene_palabra(a.cita, a.palabra):
                fallos.append(f"«{a.palabra}» no es un candidato de su cita")
            if a.rasgo not in RASGOS or a.gravedad not in GRAVEDADES:
                fallos.append(f"«{a.palabra}»: valor no permitido")
    palabras = [a.palabra.lower() for a in informe.avisos if a.clase == "lexico" and a.palabra]
    if len(palabras) != len(set(palabras)):
        fallos.append("dos avisos sobre la misma palabra")
    return fallos


# --- Informes ---------------------------------------------------------------

def escribir(nombre: str, datos) -> Path:
    ruta = SALIDA / nombre
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


def grabar_respuesta(caso: dict, pasaje: str, medida: Medida, respuesta: dict,
                     informe: Informe) -> None:
    """Lo necesario para reproducir el informe sin API."""
    escribir(f"respuestas/{caso['id']}.json", {
        "id": caso["id"], "pasaje": pasaje, "medida": asdict(medida),
        "respuesta": respuesta, "informe": asdict(informe),
    })


def resumen_filtro(filas: list[dict]) -> str:
    """filas: {id, categoria, candidatos, esperadas (palabras, solo léxico)}."""
    lexico = [f for f in filas if f["categoria"] == "lexico"]
    cubiertos = [f for f in lexico if set(f["esperadas"]) & {c.lower() for c in f["candidatos"]}]
    lineas = [f"Cobertura del filtro: {len(cubiertos)}/{len(lexico)} casos léxicos con la "
              "palabra sembrada entre los candidatos.", "Ruido (candidatos por caso):"]
    for f in filas:
        if f["categoria"] in SIN_AVISOS or f["categoria"] == "lexico":
            lineas.append(f"  {f['id']} ({f['categoria']}): {len(f['candidatos'])} "
                          f"{', '.join(f['candidatos'])}")
    return "\n".join(lineas)


def resumen_banco(filas: list[dict]) -> str:
    normas = [f for f in filas if f["categoria"] == "norma"]
    lexico = [f for f in filas if f["categoria"] == "lexico"]
    limpios = [f for f in filas if f["categoria"] in SIN_AVISOS]
    detectados = [f for f in lexico if f["resultado"].get("detectado")]
    lineas = [
        f"Normas: {sum(f['resultado']['detectado'] for f in normas)}/{len(normas)}; "
        f"avisos de norma en controles y trampas: "
        f"{sum(f['resultado']['avisos_norma'] for f in limpios)}.",
        f"Léxico: {len(detectados)}/{len(lexico)} (mínimo {MINIMO_LEXICO}); gravedad correcta "
        f"{sum(bool(f['resultado'].get('gravedad_correcta')) for f in detectados)}/{len(detectados)}.",
        f"Avisos léxicos en controles y trampas: "
        f"{sum(f['resultado']['avisos_lexico'] for f in limpios)} (máximo {MAXIMO_AVISOS_EN_LIMPIOS}).",
        f"Llamadas a la API: {sum(f.get('llamada', False) for f in filas)} de {len(filas)}; "
        f"avisos descartados por la validación: {sum(f.get('descartados', 0) for f in filas)}.",
        "Fallos:",
    ]
    for f in filas:
        r = f["resultado"]
        if f.get("error"):
            lineas.append(f"  {f['id']}: ERROR — {f['error']}")
        elif r.get("fallo"):
            lineas.append(f"  {f['id']}: del {r['fallo']}")
        elif f["categoria"] == "norma" and not r["detectado"]:
            lineas.append(f"  {f['id']}: norma sin avisar")
        elif f["categoria"] in SIN_AVISOS and not r["correcto"]:
            lineas.append(f"  {f['id']}: {r['avisos_norma'] + r['avisos_lexico']} avisos")
    return "\n".join(lineas)
