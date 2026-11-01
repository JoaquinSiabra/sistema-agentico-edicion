"""Banco de Voz y edición sin base de datos ni API.

Que cada caso se construye sobre su canto; la parte determinista del umbral
(normas: 4/4 y ninguna en controles y trampas); la cobertura y el ruido
del filtro léxico (informativos, en .cache/voz/filtro.txt); la puntuación;
y (S) que con las respuestas grabadas el informe se reproduce igual.
"""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from banco_voz import (GRABADAS, SALIDA, SIN_AVISOS, cargar_casos, construir_caso,
                       medida_desde_dict, perfil_del_banco, puntuar, resumen_filtro, texto)
from voz import Aviso, Informe, Medida, medir, validar

CASOS = cargar_casos()
por_caso = pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])


@pytest.fixture(scope="module")
def perfil():
    return perfil_del_banco()


def medida_del_caso(caso, perfil):
    fragmento = texto(caso["orden"])
    pasaje, char = construir_caso(caso, fragmento)
    return pasaje, medir(pasaje, perfil, excluir=fragmento[char:char + len(pasaje)] == pasaje)


def test_el_banco_tiene_los_casos_de_la_especificacion():
    categorias = [c["categoria"] for c in CASOS]
    assert {k: categorias.count(k) for k in set(categorias)} == {
        "norma": 4, "lexico": 6, "control": 6, "trampa": 6}


@por_caso
def test_cada_caso_se_construye_sobre_su_canto(caso):
    fragmento = texto(caso["orden"])
    pasaje, char = construir_caso(caso, fragmento)
    if "tras_verso" in caso:
        assert pasaje == caso["texto"] and fragmento[char:char + 1] in ("\n", "")
    else:
        assert fragmento[char:].startswith(pasaje)


@por_caso
def test_normas_del_banco(caso, perfil):
    """La parte determinista del umbral: cada caso de norma avisa de todas
    sus formas y ningún otro caso avisa de ninguna norma."""
    _, medida = medida_del_caso(caso, perfil)
    informe = validar({}, medida, "")
    resultado = puntuar(caso, medida, informe)
    if caso["categoria"] == "norma":
        assert resultado["detectado"]
    else:
        assert medida.avisos_norma == []


def test_cobertura_y_ruido_del_filtro(perfil):
    filas = []
    for caso in CASOS:
        _, medida = medida_del_caso(caso, perfil)
        filas.append({"id": caso["id"], "categoria": caso["categoria"],
                      "candidatos": medida.candidatos,
                      "esperadas": [p.lower() for e in caso["esperado"]
                                    for p in e.get("palabras", [])]})
    SALIDA.mkdir(parents=True, exist_ok=True)
    (SALIDA / "filtro.txt").write_text(resumen_filtro(filas) + "\n", encoding="utf-8")
    print("\n" + resumen_filtro(filas))
    assert len(filas) == len(CASOS)


# --- Puntuación --------------------------------------------------------------

def caso(identificador):
    return next(c for c in CASOS if c["id"] == identificador)


def lexico(palabra, gravedad="ruptura"):
    return Aviso("lexico", "anacronismo", palabra, gravedad, "", palabra=palabra)


def test_puntuacion_lexica():
    l01 = caso("L01")   # «reloj», ruptura
    medida = Medida([], ["reloj", "apremiaba"])
    r = puntuar(l01, medida, Informe([lexico("reloj")], []))
    assert r["detectado"] and r["gravedad_correcta"]
    assert puntuar(l01, medida, Informe([lexico("apremiaba")], []))["fallo"] == "agente"
    assert puntuar(l01, Medida([], ["apremiaba"]), Informe([], []))["fallo"] == "filtro"


def test_puntuacion_de_normas_y_controles():
    n01 = caso("N01")   # «a» y «o»
    solo_a = Informe([Aviso("norma", "ortografía", "a", "ruptura", "")], [])
    assert not puntuar(n01, Medida([], []), solo_a)["detectado"]
    control = next(c for c in CASOS if c["categoria"] in SIN_AVISOS)
    assert puntuar(control, Medida([], []), Informe([], []))["correcto"]
    assert not puntuar(control, Medida([], []), Informe([lexico("daga")], []))["correcto"]


# --- Respuestas grabadas (S) ------------------------------------------------

GRABADOS = sorted(GRABADAS.glob("*.json")) if GRABADAS.exists() else []


@pytest.mark.skipif(not GRABADOS, reason=f"aún no hay respuestas grabadas en {GRABADAS}")
@pytest.mark.parametrize("ruta", GRABADOS, ids=[r.stem for r in GRABADOS])
def test_con_respuestas_grabadas_el_informe_se_reproduce_igual(ruta: Path):
    grabado = json.loads(ruta.read_text(encoding="utf-8"))
    informe = validar(grabado["respuesta"], medida_desde_dict(grabado["medida"]), grabado["pasaje"])
    informe.llamadas = grabado["informe"]["llamadas"]
    assert json.loads(json.dumps(asdict(informe))) == grabado["informe"]
