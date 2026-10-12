"""Specs de las fases que llaman a Claude, comprobadas contra la API real.

Solo con `pytest --api` (y ANTHROPIC_API_KEY). Hace 3 llamadas sobre el
Canto I de la Ilíada, una por fase. La respuesta del modelo cambia entre
ejecuciones, así que aquí no se compara con nada grabado: se comprueban
las propiedades que TODA respuesta válida debe cumplir (ver tests/SPECS.md).
Útil al cambiar un prompt o el modelo, antes de recalibrar una obra entera.
"""

import json
import os
import re
from pathlib import Path

import pytest

import calibrar_glosario
import calibrar_taxonomia
import extraer_canon

pytestmark = pytest.mark.api

RAIZ = Path(__file__).resolve().parents[2]
FIJOS = RAIZ / "tests" / "fijos" / "iliada_cantos_i_ii"
CANTO_I = RAIZ / "tests" / "fijos" / "textos" / "iliada_canto_i.txt"
CATEGORIAS = {"persona", "lugar", "pueblo", "objeto", "organizacion",
              "concepto_abstracto", "incierto"}


@pytest.fixture(scope="module", autouse=True)
def hay_clave():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.exit("--api necesita ANTHROPIC_API_KEY en el entorno")


@pytest.fixture(scope="module")
def glosario() -> list[dict]:
    return calibrar_glosario.calibrar([str(CANTO_I)])["glosario"]


@pytest.fixture(scope="module")
def taxonomia() -> dict:
    return calibrar_taxonomia.calibrar([str(CANTO_I)])


@pytest.fixture(scope="module")
def config() -> dict:
    """Calibración grabada de los Cantos I-II: la extracción se prueba con
    una config fija para que solo varíe la respuesta de esta fase."""
    return {
        "tipos_documentales": json.loads(
            (FIJOS / "tipos_documentales_obra.json").read_text(encoding="utf-8")
        )["tipos_documentales"],
        **{clave: json.loads((FIJOS / f"{clave}_obra.json").read_text(encoding="utf-8"))
           for clave in ("personajes", "lugares", "glosario")},
    }


@pytest.fixture(scope="module")
def canon(config, tmp_path_factory) -> dict:
    return extraer_canon.extraer_canon(CANTO_I.read_text(encoding="utf-8"), config,
                                       directorio_debug=str(tmp_path_factory.mktemp("debug")))


# --- Fase 0b ---------------------------------------------------------------

def test_glosario_entradas_bien_formadas(glosario):
    assert glosario
    for e in glosario:
        assert e["termino"].strip()
        assert e["categoria"] in CATEGORIAS, e
        assert e["confianza"] in {"alta", "media", "baja"}, e
        assert e["evidencia"].strip()
        assert e["termino"] not in e["variantes"], e


def test_glosario_terminos_aparecen_en_el_texto(glosario):
    texto = CANTO_I.read_text(encoding="utf-8")
    inventados = [e["termino"] for e in glosario
                  if not any(f in texto for f in [e["termino"], *e["variantes"]])]
    assert not inventados


def test_glosario_encuentra_a_los_protagonistas(glosario):
    personas = {f for e in glosario if e["categoria"] == "persona"
                for f in [e["termino"], *e["variantes"]]}
    for nombre in ("Aquiles", "Agamenón", "Crises", "Briseida", "Calcas"):
        assert any(re.search(rf"\b{nombre}\b", f) for f in personas), nombre


# --- Fase 0a ---------------------------------------------------------------

def test_taxonomia_bien_formada(taxonomia):
    tipos = taxonomia["tipos_documentales"]
    assert tipos
    for t in tipos:
        assert t["nombre"].strip() and t["descripcion"].strip()
    assert len({t["nombre"] for t in tipos}) == len(tipos)


# --- Fase 1 ----------------------------------------------------------------

def test_canon_tiene_hechos_bien_atribuidos(canon):
    validos = set(re.findall(r'^\s+- "(\w+)": ', extraer_canon.PROMPT_SISTEMA_BASE, re.M))
    assert canon["hechos_canonicos"]
    for h in canon["hechos_canonicos"]:
        assert h["hecho"].strip() and h["momento_canonico"].strip()
        assert h["atribucion"]["tipo"] in validos, h
        assert (h["atribucion"]["personaje"] is None) == (h["atribucion"]["tipo"] == "narrador_documental"), h


def test_canon_usa_un_tipo_documental_calibrado(canon, config):
    assert canon["tipo_documental"] in {t["nombre"] for t in config["tipos_documentales"]}


def test_canon_nombra_con_la_forma_canonica(canon, config):
    # Spec del prompt: "usa SIEMPRE la forma canónica indicada aquí (no una variante)".
    canonica_de = {}
    for e in config["personajes"] + config["lugares"] + config["glosario"]:
        for forma in [*e.get("variantes", []), *e.get("alias", [])]:
            canonica_de.setdefault(forma, e["termino"])
    terminos = {e["termino"] for e in config["personajes"] + config["lugares"] + config["glosario"]}
    nombrados = (canon["personajes"] + canon["lugares"] + canon["organizaciones"]
                 + [h["atribucion"]["personaje"] for h in canon["hechos_canonicos"]
                    if h["atribucion"]["personaje"]])
    mal = sorted({(n, canonica_de[n]) for n in nombrados if n in canonica_de and n not in terminos})
    assert not mal, f"variantes en lugar de la forma canónica: {mal}"
