"""Banco de casos del agente de Continuidad (tests/fijos/continuidad/casos.json).

Lo que comparten las pruebas del banco: construir cada caso sobre el texto
del fragmento, medir la cobertura del contexto (¿llegó algún hecho
esperado, y por qué vía?), puntuar el informe del agente, clasificar cada
fallo como del buscador o del agente, y escribir el informe del banco.

El informe y las respuestas en bruto se escriben en NOVELA_SALIDA_CONTINUIDAD
(por defecto .cache/continuidad/, fuera de git). Las respuestas grabadas
de la suite rápida se leen de tests/fijos/continuidad/respuestas/: para
fijarlas, se copian ahí a mano las de una ejecución revisada.

Con NOVELA_BANCO_SOLO_CAMBIADOS=1, un caso cuyo contexto tiene los mismos
hechos que su respuesta grabada reutiliza esa respuesta en vez de llamar
al modelo (docs/buscador_hibrido.md, «Comprobación final con la API»).
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from almacen_prueba import FIJOS, FIJOS_III_IV, exports_de_prueba, obra_aparte
from continuidad import Contexto, Informe, consultar_modelo, normalizar

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

SOLO_CAMBIADOS = os.environ.get("NOVELA_BANCO_SOLO_CAMBIADOS") == "1"

# Combinaciones de vías y tope que compara el informe del buscador
# (docs/buscador_hibrido.md, «Cómo se mide»).
COMBINACIONES = {
    "actual": (("entidades",), 200),
    "+entorno": (("entidades", "entorno"), 200),
    "+lexica": (("entidades", "lexica"), 200),
    "híbrido": (("entidades", "entorno", "lexica"), 200),
    "híbrido-60": (("entidades", "entorno", "lexica"), 60),
}
PUESTO_MAXIMO = 60


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


def respuesta_grabada(caso_id: str, contexto: Contexto, grabadas: Path = GRABADAS) -> dict | None:
    """La respuesta grabada del caso si su contexto tenía los mismos hechos."""
    ruta = grabadas / f"{caso_id}.json"
    if not ruta.exists():
        return None
    grabado = json.loads(ruta.read_text(encoding="utf-8"))
    if {h["id"] for h in grabado["contexto"]["hechos"]} != {h.id for h in contexto.hechos}:
        return None
    return grabado["respuesta"]


def obtener_respuesta(caso_id: str, contexto: Contexto, pasaje: str,
                      solo_cambiados: bool = SOLO_CAMBIADOS,
                      grabadas: Path = GRABADAS) -> tuple[dict, bool]:
    """(respuesta, si se llamó al modelo)."""
    if solo_cambiados:
        respuesta = respuesta_grabada(caso_id, contexto, grabadas)
        if respuesta is not None:
            return respuesta, False
    return consultar_modelo(contexto, pasaje), True


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
        f"Llamadas a la API: {sum(r.get('llamada', False) for r in resultados)} "
        f"de {len(resultados)}" + (" (solo los casos con contexto cambiado)."
                                   if SOLO_CAMBIADOS else "."),
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


# --- Informe del buscador (sin modelo) --------------------------------------

def medir_buscador(caso: dict, contexto: Contexto) -> dict:
    """Por caso y combinación: si llega algún hecho esperado, en qué puesto
    del ranking fusionado queda el mejor, y cuántos hechos tiene el contexto."""
    puestos = [h.puesto for h in contexto.hechos if h.id in esperados(caso)]
    return {"llega": bool(puestos), "puesto": min(puestos) if puestos else None,
            "hechos": len(contexto.hechos)}


def tabla_buscador(medidas: dict[str, dict[str, dict]], casos: list[dict]) -> str:
    """medidas[combinación][caso] -> medir_buscador. Tabla de texto con una
    fila por caso y una columna por combinación, y el resumen."""
    nombres = list(medidas)
    con = [c for c in casos if c["categoria"] in CON_CONTRADICCION]
    sin = [c for c in casos if c["categoria"] in SIN_CONTRADICCION]

    def celda(m: dict, caso: dict) -> str:
        if caso["categoria"] in SIN_CONTRADICCION:
            return f"{m['hechos']} h"
        return (f"#{m['puesto']} / {m['hechos']} h" if m["llega"]
                else f"NO / {m['hechos']} h")

    def media(nombre: str, grupo: list[dict]) -> str:
        return f"{sum(medidas[nombre][c['id']]['hechos'] for c in grupo) / len(grupo):.1f}"

    filas = [["caso", *nombres]]
    filas += [[c["id"], *(celda(medidas[n][c["id"]], c) for n in nombres)] for c in casos]
    filas.append(["cobertura", *(f"{sum(medidas[n][c['id']]['llega'] for c in con)}/{len(con)}"
                                 for n in nombres)])
    filas.append(["peor puesto", *(str(max((medidas[n][c["id"]]["puesto"] or 0) for c in con))
                                   for n in nombres)])
    filas.append(["contexto medio", *(media(n, casos) for n in nombres)])
    filas.append(["medio con contradicción", *(media(n, con) for n in nombres)])
    filas.append(["medio controles y trampas", *(media(n, sin) for n in nombres)])
    anchos = [max(len(f[i]) for f in filas) for i in range(len(filas[0]))]
    lineas = ["  ".join(x.ljust(a) for x, a in zip(f, anchos)).rstrip() for f in filas]
    lineas.insert(1, "  ".join("-" * a for a in anchos))
    lineas.append("")
    lineas.append("Celdas: #puesto del mejor hecho esperado en el ranking fusionado / "
                  "hechos en el contexto; NO = no llega ninguno.")
    if {"actual", "híbrido", "híbrido-60"} <= set(medidas):
        lineas.append(criterio_hibrido_60(medidas, casos))
    return "\n".join(lineas)


def criterio_hibrido_60(medidas: dict[str, dict[str, dict]], casos: list[dict]) -> str:
    """Criterio para cambiar el valor por defecto a híbrido-60: cobertura
    16/16, ningún hecho esperado por debajo del puesto 60 (en el ranking
    híbrido, con tope 200) y contexto medio menor que el actual."""
    con = [c for c in casos if c["categoria"] in CON_CONTRADICCION]
    cobertura_total = all(medidas["híbrido-60"][c["id"]]["llega"] for c in con)
    puestos = [medidas["híbrido"][c["id"]]["puesto"] for c in con]
    en_puesto = all(p is not None and p <= PUESTO_MAXIMO for p in puestos)

    def media(nombre):
        return sum(m["hechos"] for m in medidas[nombre].values()) / len(medidas[nombre])
    menor = media("híbrido-60") < media("actual")
    cumple = cobertura_total and en_puesto and menor
    return (f"Criterio híbrido-60: cobertura completa {'sí' if cobertura_total else 'no'}; "
            f"todos en el puesto {PUESTO_MAXIMO} o antes {'sí' if en_puesto else 'no'}; "
            f"contexto medio menor que el actual {'sí' if menor else 'no'} -> "
            f"{'CUMPLE' if cumple else 'no cumple'}.")

