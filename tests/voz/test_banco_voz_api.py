"""Banco del agente de Voz y edición contra la API real, con coste.

    pytest --api tests/voz

Una llamada por caso con candidatos léxicos (los casos sin candidatos no
llaman). No necesita base de datos: el perfil sale de tests/fijos/textos/.
Escribe en .cache/voz/ (o en NOVELA_SALIDA_VOZ) el informe (banco.json y
banco.txt), con cada fallo léxico clasificado como del filtro o del agente,
y las respuestas en bruto (respuestas/<caso>.json). Para que la suite
rápida las reproduzca, se copian a mano a tests/fijos/voz/respuestas/.

Umbral (docs/agente_voz.md): léxico ≥ 5/6, como mucho 1 aviso léxico en
controles y trampas, y todos los avisos cumplen las garantías. Las normas
se comprueban sin API en test_banco_voz_casos.py.
"""

import os
from dataclasses import asdict

import pytest

from banco_voz import (MAXIMO_AVISOS_EN_LIMPIOS, MINIMO_LEXICO, SALIDA, SIN_AVISOS,
                       cargar_casos, construir_caso, escribir, garantias_incumplidas,
                       grabar_respuesta, perfil_del_banco, puntuar, resumen_banco, texto)
from voz import CARACTERES_ENTORNO, Entorno, consultar_modelo, medir, validar

pytestmark = pytest.mark.api


@pytest.fixture(scope="module")
def resultados() -> list[dict]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.exit("--api necesita ANTHROPIC_API_KEY en el entorno")
    perfil, filas = perfil_del_banco(), []
    for caso in cargar_casos():
        fragmento = texto(caso["orden"])
        pasaje, char = construir_caso(caso, fragmento)
        medida = medir(pasaje, perfil, excluir=fragmento[char:char + len(pasaje)] == pasaje)
        fila = {"id": caso["id"], "categoria": caso["categoria"], "llamada": bool(medida.candidatos)}
        respuesta = {}
        if medida.candidatos:
            entorno = Entorno(antes=fragmento[max(0, char - CARACTERES_ENTORNO):char],
                              despues=fragmento[char:char + CARACTERES_ENTORNO])
            try:
                respuesta = consultar_modelo(medida, entorno, pasaje)
            except Exception as e:     # JSON roto o error de la API: se informa
                filas.append({**fila, "error": repr(e), "resultado": {
                    "avisos_norma": len(medida.avisos_norma), "avisos_lexico": 0,
                    "correcto": False, "detectado": False}, "garantias": []})
                continue
        informe = validar(respuesta, medida, pasaje)
        informe.llamadas = int(fila["llamada"])
        grabar_respuesta(caso, pasaje, medida, respuesta, informe)
        filas.append({**fila, "resultado": puntuar(caso, medida, informe),
                      "descartados": len(informe.descartados),
                      "garantias": garantias_incumplidas(informe, medida, pasaje),
                      "informe": asdict(informe)})
    escribir("banco.json", filas)
    resumen = resumen_banco(filas)
    (SALIDA / "banco.txt").write_text(resumen + "\n", encoding="utf-8")
    print(f"\n{resumen}\n(detalle en {SALIDA})")
    return filas


def test_ninguna_llamada_falla(resultados):
    assert [r["id"] for r in resultados if r.get("error")] == []


def test_lexico_al_menos_5_de_6(resultados):
    lexico = [r for r in resultados if r["categoria"] == "lexico"]
    assert len(lexico) == 6
    assert sum(bool(r["resultado"].get("detectado")) for r in lexico) >= MINIMO_LEXICO


def test_como_mucho_un_aviso_lexico_en_controles_y_trampas(resultados):
    limpios = [r for r in resultados if r["categoria"] in SIN_AVISOS]
    assert len(limpios) == 12
    assert sum(r["resultado"]["avisos_lexico"] for r in limpios) <= MAXIMO_AVISOS_EN_LIMPIOS


def test_todos_los_avisos_cumplen_las_garantias(resultados):
    assert {r["id"]: r["garantias"] for r in resultados if r["garantias"]} == {}


def test_cada_fallo_lexico_clasificado(resultados):
    for r in resultados:
        if r["categoria"] == "lexico" and not r.get("error") and not r["resultado"]["detectado"]:
            assert r["resultado"]["fallo"] in {"filtro", "agente"}
