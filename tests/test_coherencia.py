"""Coherencia entre piezas que nada enlaza automáticamente.

El 3 de octubre la ingesta falló porque el prompt de extraer_canon.py ya
usaba tres categorías de atribución y el CHECK de schema.sql seguía con
dos. Estas pruebas detectan ese tipo de desajuste antes de tocar la base
de datos.
"""

import json
import re
from pathlib import Path

import pytest

import extraer_canon

RAIZ = Path(__file__).resolve().parents[1]
FIJOS = RAIZ / "tests" / "fijos" / "iliada_cantos_i_ii"

# Cada canon con el directorio de su glosario: las respuestas grabadas de
# tests/fijos y, si existen, los canon de las obras en data/<obra>/exports.
CANON = [(r, FIJOS) for r in sorted((FIJOS / "respuestas_canon").glob("*.json"))] + [
    (r, r.parents[1]) for r in sorted(RAIZ.glob("data/*/exports/canon/*.json"))]
CANON_DE_OBRAS = [(r, d) for r, d in CANON if d != FIJOS]


def nombre(par) -> str:
    ruta, directorio = par
    return f"{'fijos' if directorio == FIJOS else directorio.parent.name}/{ruta.name}"


def leer(ruta: Path):
    return json.loads(ruta.read_text(encoding="utf-8"))


def tipos_en_schema() -> set[str]:
    schema = (RAIZ / "schema.sql").read_text(encoding="utf-8")
    m = re.search(r"atribucion_tipo IN \((.*?)\)", schema, re.S)
    assert m, "no se encuentra el CHECK de atribucion_tipo en schema.sql"
    return set(re.findall(r"'(\w+)'", m.group(1)))


def tipos_en_prompt() -> set[str]:
    return set(re.findall(r'^\s+- "(\w+)": ', extraer_canon.PROMPT_SISTEMA_BASE, re.M))


def test_toda_categoria_del_prompt_la_acepta_el_schema():
    assert tipos_en_prompt(), "no se encuentran las categorías de atribución en el prompt"
    assert tipos_en_prompt() <= tipos_en_schema()


def test_parsear_args_de_extraer_canon():
    obra, config, orden, ruta = extraer_canon.parsear_args(
        ["--obra=iliada", "--orden=3", "iliada_canto_iii.txt"])
    assert (obra, config, orden, ruta) == ("iliada", None, 3, "iliada_canto_iii.txt")


@pytest.mark.parametrize("par", CANON_DE_OBRAS, ids=map(nombre, CANON_DE_OBRAS))
def test_canon_trae_lo_que_necesita_la_ingesta(par):
    ruta, _ = par
    canon = leer(ruta)
    assert canon["orden_fragmento"] == int(ruta.stem)
    assert canon["ruta_fragmento_origen"]


@pytest.mark.parametrize("par", CANON, ids=map(nombre, CANON))
def test_hechos_con_atribucion_valida_para_el_schema(par):
    validos = tipos_en_schema()
    hechos = leer(par[0])["hechos_canonicos"]
    assert hechos
    for hecho in hechos:
        assert hecho["hecho"] and hecho["momento_canonico"]
        tipo, personaje = hecho["atribucion"]["tipo"], hecho["atribucion"]["personaje"]
        assert tipo in validos, hecho["hecho"]
        assert (personaje is None) == (tipo == "narrador_documental"), hecho["hecho"]


@pytest.mark.parametrize("par", CANON, ids=map(nombre, CANON))
def test_quien_afirma_un_hecho_es_un_personaje_del_glosario(par):
    # cargar_hechos busca al personaje por término canónico exacto; si no lo
    # encuentra, guarda la atribución sin personaje y no avisa.
    ruta, directorio = par
    personajes = {e["termino"] for e in leer(directorio / "personajes_obra.json")}
    desconocidos = {h["atribucion"]["personaje"] for h in leer(ruta)["hechos_canonicos"]
                    if h["atribucion"]["personaje"]} - personajes
    assert not desconocidos, f"no son términos canónicos del glosario: {sorted(desconocidos)}"
