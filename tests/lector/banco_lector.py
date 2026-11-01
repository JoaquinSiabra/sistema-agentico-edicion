"""Banco del buscador del lector (tests/fijos/lector/casos.json).

Construir el punto de lectura de cada caso, cargar los Cantos I-IV como una
obra aparte en la base de prueba, medir cada caso y escribir el informe en
NOVELA_SALIDA_LECTOR (por defecto .cache/lector/).
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from almacen_prueba import FIJOS, FIJOS_III_IV, exports_de_prueba, obra_aparte

RAIZ = Path(__file__).resolve().parents[2]
CASOS = RAIZ / "tests" / "fijos" / "lector" / "casos.json"
TEXTOS = RAIZ / "tests" / "fijos" / "textos"
CANTOS = {1: "i", 2: "ii", 3: "iii", 4: "iv"}
SALIDA = Path(os.environ.get("NOVELA_SALIDA_LECTOR", RAIZ / ".cache" / "lector"))
OBRA = "iliada_lector"

# Umbral (docs/buscador_lector.md, «Cómo se mide»): sin fugas, y todas las
# preguntas de memoria y los controles cubiertos.
CUBIERTAS = {"memoria", "control"}


def cargar_casos() -> list[dict]:
    return json.loads(CASOS.read_text(encoding="utf-8"))["casos"]


def texto(orden: int) -> str:
    return (TEXTOS / f"iliada_canto_{CANTOS[orden]}.txt").read_text(encoding="utf-8")


def hecho_id(referencia: str) -> int:
    orden, indice = referencia.split(".")
    return int(orden) * 10000 + int(indice)


def punto(caso: dict, fragmento: str) -> tuple[int, int | None]:
    """(hasta_fragmento, hasta_char): el fragmento entero o, con `verso`,
    hasta el comienzo de la línea de ese verso, sin incluirla."""
    p = caso["punto"]
    if "verso" not in p:
        return p["orden"], None
    m = re.search(rf"^{p['verso']} ", fragmento, re.M)
    if not m:
        raise ValueError(f"{caso['id']}: no hay ninguna línea que empiece por «{p['verso']} ».")
    return p["orden"], m.start()


def control_de(caso: dict, casos: list[dict]) -> dict | None:
    return next((c for c in casos if c["id"] == caso["id"] + "c"), None)


@contextmanager
def obra_del_banco(bd, modelo, destino: Path):
    """Los Cantos I-IV con el glosario tras el Canto IV, como una obra
    aparte en la base de prueba (se borra al terminar)."""
    exports = exports_de_prueba(destino, glosario=FIJOS_III_IV, canon_de={
        1: FIJOS, 2: FIJOS, 3: FIJOS_III_IV, 4: FIJOS_III_IV})
    with obra_aparte(bd, modelo, OBRA, exports) as con:
        yield con


def medir(caso: dict, respuesta) -> dict:
    """Por caso: si llega algún hecho esperado y en qué puesto queda el
    mejor, qué fugas llegan (debe ser ninguna) y cuántos hechos y pasajes."""
    esperados = {hecho_id(r) for r in caso.get("esperados", [])}
    fugas = {hecho_id(r) for r in caso.get("fugas", [])}
    puestos = [h.puesto for h in respuesta.hechos if h.id in esperados]
    return {
        "id": caso["id"], "categoria": caso["categoria"],
        "cubierto": bool(puestos) if esperados else None,
        "puesto": min(puestos) if puestos else None,
        "fugas": sorted(h.id for h in respuesta.hechos if h.id in fugas),
        "hechos": len(respuesta.hechos), "omitidos": respuesta.omitidos,
        "pasajes": len(respuesta.pasajes), "vias": respuesta.candidatos,
    }


def resumen(filas: list[dict]) -> str:
    cubiertas = [f for f in filas if f["categoria"] in CUBIERTAS]
    lineas = [
        f"Memoria y controles cubiertos: {sum(bool(f['cubierto']) for f in cubiertas)}"
        f"/{len(cubiertas)}.",
        f"Fugas: {sum(len(f['fugas']) for f in filas)}.",
        "",
        "caso   categoría  cubierto  puesto  hechos  omitidos  pasajes",
    ]
    for f in filas:
        cubierto = "—" if f["cubierto"] is None else ("sí" if f["cubierto"] else "NO")
        lineas.append(f"{f['id']:6} {f['categoria']:10} {cubierto:9} {str(f['puesto'] or '—'):7} "
                      f"{f['hechos']:6}  {f['omitidos']:8}  {f['pasajes']:7}")
    return "\n".join(lineas)


def escribir(filas: list[dict]) -> Path:
    SALIDA.mkdir(parents=True, exist_ok=True)
    (SALIDA / "buscador.json").write_text(json.dumps(filas, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    ruta = SALIDA / "buscador.txt"
    ruta.write_text(resumen(filas) + "\n", encoding="utf-8")
    return ruta
