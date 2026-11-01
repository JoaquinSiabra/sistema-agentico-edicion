"""Banco del buscador del lector sin base de datos: que cada caso se
construye y que el banco es coherente (lo que se espera va antes del punto,
lo que no puede llegar va después, y cada spoiler tiene su control)."""

import json

import pytest

from banco_lector import (CASOS, RAIZ, cargar_casos, control_de, hecho_id, punto, resumen,
                          texto)

CASOS_BANCO = cargar_casos()
por_caso = pytest.mark.parametrize("caso", CASOS_BANCO, ids=[c["id"] for c in CASOS_BANCO])
CANON = {
    1: RAIZ / "tests/fijos/iliada_cantos_i_ii/respuestas_canon/0001.json",
    2: RAIZ / "tests/fijos/iliada_cantos_i_ii/respuestas_canon/0002.json",
    3: RAIZ / "tests/fijos/iliada_cantos_iii_iv/respuestas_canon/0003.json",
    4: RAIZ / "tests/fijos/iliada_cantos_iii_iv/respuestas_canon/0004.json",
}
HECHOS = {o: len(json.loads(r.read_text(encoding="utf-8"))["hechos_canonicos"])
          for o, r in CANON.items()}


def test_el_banco_tiene_los_casos_de_la_especificacion():
    categorias = [c["categoria"] for c in CASOS_BANCO]
    assert {k: categorias.count(k) for k in set(categorias)} == {
        "memoria": 6, "parcial": 2, "spoiler": 4, "control": 4}
    assert CASOS.name == "casos.json"


@por_caso
def test_cada_punto_de_lectura_se_construye(caso):
    hasta_fragmento, hasta_char = punto(caso, texto(caso["punto"]["orden"]))
    assert hasta_fragmento == caso["punto"]["orden"]
    if "verso" in caso["punto"]:
        assert texto(hasta_fragmento)[hasta_char:].startswith(f"{caso['punto']['verso']} ")
    else:
        assert hasta_char is None


@por_caso
def test_las_referencias_existen_y_respetan_el_punto(caso):
    for r in caso.get("esperados", []) + caso.get("fugas", []):
        orden, indice = map(int, r.split("."))
        assert indice < HECHOS[orden], r
    ultimo = caso["punto"]["orden"]
    for r in caso.get("esperados", []):
        assert hecho_id(r) // 10000 <= ultimo, r
    for r in caso.get("fugas", []):
        # Posterior al punto: de un canto siguiente, o del canto en curso si
        # el punto está a mitad (el desenlace va después del verso).
        assert hecho_id(r) // 10000 > ultimo or "verso" in caso["punto"], r


@pytest.mark.parametrize("caso", [c for c in CASOS_BANCO if c["categoria"] == "spoiler"],
                         ids=lambda c: c["id"])
def test_cada_spoiler_tiene_un_control_que_espera_sus_fugas(caso):
    control = control_de(caso, CASOS_BANCO)
    assert control and control["categoria"] == "control"
    assert control["consulta"] == caso["consulta"]
    assert set(control["esperados"]) <= set(caso["fugas"])


def test_resumen_del_informe():
    filas = [{"id": "M01", "categoria": "memoria", "cubierto": True, "puesto": 3, "fugas": [],
              "hechos": 31, "omitidos": 0, "pasajes": 5, "vias": {}},
             {"id": "E01", "categoria": "spoiler", "cubierto": None, "puesto": None,
              "fugas": [30044], "hechos": 4, "omitidos": 0, "pasajes": 5, "vias": {}}]
    texto_resumen = resumen(filas)
    assert "Memoria y controles cubiertos: 1/1." in texto_resumen
    assert "Fugas: 1." in texto_resumen
