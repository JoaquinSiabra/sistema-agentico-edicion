"""Banco de casos del agente de Continuidad (tests/fijos/continuidad/casos.json).

Lo que comparten las pruebas del banco: construir cada caso sobre el texto
del fragmento, medir la cobertura del contexto (¿llegó algún hecho
esperado, y por qué vía?), puntuar el informe del agente, clasificar cada
fallo como del buscador o del agente, y escribir el informe del banco.

El informe y las respuestas en bruto se escriben en NOVELA_SALIDA_CONTINUIDAD
(por defecto .cache/continuidad/, fuera de git). Las respuestas grabadas
de la suite rápida se leen de tests/fijos/continuidad/respuestas/: para
fijarlas, se copian ahí a mano las de una ejecución revisada.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from almacen_prueba import FIJOS, FIJOS_III_IV, exports_de_prueba, obra_aparte
from continuidad import Contexto, Informe, normalizar

RAIZ = Path(__file__).resolve().parents[2]
CASOS = RAIZ / "tests" / "fijos" / "continuidad" / "casos.json"
GRABADAS = RAIZ / "tests" / "fijos" / "continuidad" / "respuestas"
GLOSARIO = FIJOS_III_IV
SALIDA = Path(os.environ.get("NOVELA_SALIDA_CONTINUIDAD", RAIZ / ".cache" / "continuidad"))
OBRA = "iliada_continuidad"

# Umbral de aceptación (docs/agente_continuidad.md, «Cómo se mide»).
MINIMO_DETECTADOS = 13
MAXIMO_AVISOS_EN_LIMPIOS = 1

CON_CONTRADICCION = {"sembrado", "direccion"}
SIN_CONTRADICCION = {"control", "trampa"}


@contextmanager
def obra_del_banco(bd, modelo, destino: Path):
    """Los Cantos I-IV con el glosario tras el Canto IV, como una obra
    aparte en la base de prueba (se borra al terminar)."""
    exports = exports_de_prueba(destino, glosario=FIJOS_III_IV, canon_de={
        1: FIJOS, 2: FIJOS, 3: FIJOS_III_IV, 4: FIJOS_III_IV})
    with obra_aparte(bd, modelo, OBRA, exports) as con:
        yield con


def cargar_casos() -> list[dict]:
    return json.loads(CASOS.read_text(encoding="utf-8"))["casos"]


def hecho_id(referencia: str) -> int:
    """«<orden>.<índice>» -> posicion_global."""
    orden, indice = referencia.split(".")
    return int(orden) * 10000 + int(indice)


def esperados(caso: dict) -> set[int]:
    return {hecho_id(r) for e in caso["esperado"] for r in e["hechos"]}


def construir_caso(caso: dict, texto: str) -> tuple[str, int]:
    """(pasaje, char) de un caso sobre el texto del fragmento. Con
    `tras_verso`, el pasaje es `texto` y se inserta al final del párrafo
    que empieza por ese verso; con solo `verso`, el pasaje es ese párrafo
    original, evaluado en su inicio."""
    verso = caso.get("tras_verso", caso.get("verso"))
    m = re.search(rf"^{verso} .*$", texto, re.M)
    if not m:
        raise ValueError(f"{caso['id']}: no hay ninguna línea que empiece por «{verso} ».")
    if "tras_verso" in caso:
        return caso["texto"], m.end()
    return m.group(0), m.start()


# --- Cobertura del contexto (sin modelo) ------------------------------------

def formas_no_identificadoras() -> dict[str, list[str]]:
    """Epítetos y formas compartidas del glosario, con las entidades a las
    que se refieren: no identifican a nadie, pero explican un fallo."""
    formas: dict[str, list[str]] = {}
    for nombre in ("personajes_obra.json", "lugares_obra.json", "glosario_obra.json"):
        for e in json.loads((GLOSARIO / nombre).read_text(encoding="utf-8")):
            for epiteto in e.get("epitetos") or []:
                formas.setdefault(epiteto, []).append(e["termino"])
    discrepancias = json.loads(
        (GLOSARIO / "discrepancias_calibracion.json").read_text(encoding="utf-8"))
    for d in discrepancias:
        if d.get("motivo") == "forma compartida":
            formas.setdefault(d["termino"], []).extend(d["entidades"])
    return formas


def entidades_detectadas(contexto: Contexto) -> list[str]:
    return sorted(o.removeprefix("entidad: ") for o in contexto.candidatos
                  if o.startswith("entidad: "))


def motivo_fallo_buscador(pasaje: str, contexto: Contexto) -> str:
    """El motivo observable de que no llegara ningún hecho esperado."""
    por_epiteto = sorted(
        f"«{forma}» ({', '.join(entidades)})"
        for forma, entidades in formas_no_identificadoras().items()
        if re.search(rf"(?<!\w){re.escape(forma)}(?!\w)", pasaje, re.IGNORECASE))
    if por_epiteto:
        return "la entidad se nombra solo por un epíteto o una forma compartida: " \
            + "; ".join(por_epiteto)
    detectadas = entidades_detectadas(contexto)
    if not detectadas:
        return "ninguna entidad detectada en el pasaje"
    return ("el hecho nombra a la entidad de otra forma o solo se relaciona por el tema "
            f"(revisar a mano; entidades detectadas: {', '.join(detectadas)})")


def cobertura(caso: dict, pasaje: str, contexto: Contexto) -> dict:
    llegados = {h.id: h.origen for h in contexto.hechos if h.id in esperados(caso)}
    resultado = {
        "id": caso["id"], "categoria": caso["categoria"],
        "entidades": entidades_detectadas(contexto),
        "hechos_en_contexto": len(contexto.hechos),
        "hechos_omitidos": contexto.hechos_omitidos,
        "llegaron": {str(i): o for i, o in sorted(llegados.items())},
    }
    if not llegados:
        resultado["motivo"] = motivo_fallo_buscador(pasaje, contexto)
    return resultado


# --- Resultado del agente ---------------------------------------------------

def garantias_incumplidas(informe: Informe, contexto: Contexto, pasaje: str) -> list[str]:
    """Las garantías de «Validar», comprobadas sobre el informe final."""
    fallos = []
    for a in informe.avisos:
        h = contexto.hecho(a.hecho)
        if normalizar(a.cita) not in normalizar(pasaje):
            fallos.append(f"[{a.hecho}] cita no literal: {a.cita!r}")
        if h is None:
            fallos.append(f"[{a.hecho}] hecho fuera del contexto")
            continue
        if not h.del_narrador:
            fallos.append(f"[{a.hecho}] hecho que no es del narrador")
        if h.direccion != "mismo_fragmento" and a.direccion != h.direccion:
            fallos.append(f"[{a.hecho}] dirección {a.direccion}, debía ser {h.direccion}")
        if a.tipo not in {"hecho", "estado"} or a.gravedad not in {"contradiccion", "tension"} \
                or a.direccion not in {"anterior", "posterior"}:
            fallos.append(f"[{a.hecho}] valor no permitido")
    return fallos


def puntuar(caso: dict, contexto: Contexto, informe: Informe) -> dict:
    """Detectado: algún aviso cita un hecho esperado. Gravedad y dirección
    se informan aparte. Un caso no detectado es fallo del buscador si no
    llegó ningún hecho esperado al contexto, y del agente si llegó."""
    if caso["categoria"] in SIN_CONTRADICCION:
        return {"avisos": len(informe.avisos), "correcto": not informe.avisos}
    citados = [a for a in informe.avisos if a.hecho in esperados(caso)]
    resultado = {"avisos": len(informe.avisos), "detectado": bool(citados)}
    if citados:
        [esperado] = caso["esperado"]
        resultado["gravedad_correcta"] = any(a.gravedad == esperado["gravedad"] for a in citados)
        resultado["direccion_correcta"] = any(a.direccion == esperado["direccion"]
                                              for a in citados)
    elif any(h.id in esperados(caso) for h in contexto.hechos):
        resultado["fallo"] = "agente"
    else:
        resultado["fallo"] = "buscador"
    return resultado


# --- Informes ---------------------------------------------------------------

def escribir(nombre: str, datos) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)
    ruta = SALIDA / nombre
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta


def grabar_respuesta(caso: dict, pasaje: str, contexto: Contexto, respuesta: dict,
                     informe: Informe) -> None:
    """Lo necesario para reproducir el informe sin base de datos ni API."""
    (SALIDA / "respuestas").mkdir(parents=True, exist_ok=True)
    escribir(f"respuestas/{caso['id']}.json", {
        "id": caso["id"], "pasaje": pasaje, "contexto": asdict(contexto),
        "respuesta": respuesta, "informe": asdict(informe),
    })


def resumen_cobertura(filas: list[dict]) -> str:
    cubiertos = [f for f in filas if f["llegaron"]]
    lineas = [f"Cobertura del contexto: {len(cubiertos)}/{len(filas)} casos con "
              "contradicción reciben algún hecho esperado."]
    for f in filas:
        if f["llegaron"]:
            vias = sorted({v for o in f["llegaron"].values() for v in o})
            lineas.append(f"  {f['id']}: sí ({', '.join(vias)})")
        else:
            lineas.append(f"  {f['id']}: NO — {f['motivo']}")
    return "\n".join(lineas)


def resumen_banco(resultados: list[dict]) -> str:
    con = [r for r in resultados if r["categoria"] in CON_CONTRADICCION]
    sin = [r for r in resultados if r["categoria"] in SIN_CONTRADICCION]
    detectados = [r for r in con if r["resultado"].get("detectado")]
    lineas = [
        f"Detectados: {len(detectados)}/{len(con)} (mínimo {MINIMO_DETECTADOS}).",
        f"Avisos en controles y trampas: {sum(r['resultado']['avisos'] for r in sin)} "
        f"(máximo {MAXIMO_AVISOS_EN_LIMPIOS}).",
        f"Gravedad correcta: {sum(bool(r['resultado'].get('gravedad_correcta')) for r in detectados)}"
        f"/{len(detectados)}; dirección correcta: "
        f"{sum(bool(r['resultado'].get('direccion_correcta')) for r in detectados)}"
        f"/{len(detectados)}.",
        f"Avisos descartados por la validación: {sum(r['descartados'] for r in resultados)}.",
        "Fallos:",
    ]
    for r in resultados:
        if r.get("error"):
            lineas.append(f"  {r['id']}: ERROR — {r['error']}")
        elif r["resultado"].get("fallo") == "buscador":
            lineas.append(f"  {r['id']}: del buscador — {r['cobertura']['motivo']}")
        elif r["resultado"].get("fallo") == "agente":
            lineas.append(f"  {r['id']}: del agente (el hecho llegó y no lo citó)")
        elif r["categoria"] in SIN_CONTRADICCION and not r["resultado"]["correcto"]:
            lineas.append(f"  {r['id']}: {r['resultado']['avisos']} avisos en un "
                          f"{r['categoria']}")
    return "\n".join(lineas)
