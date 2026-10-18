"""Banco del agente de Continuidad: base de datos + API real, con coste.

    pytest --db --api tests/continuidad

27 llamadas a Claude, una por caso. Con NOVELA_BANCO_SOLO_CAMBIADOS=1,
solo las de los casos cuyo contexto ha cambiado respecto a su respuesta
grabada; el informe dice cuántas se hicieron. Escribe en .cache/continuidad/ (o en
NOVELA_SALIDA_CONTINUIDAD) el informe del banco (banco.json y banco.txt),
con cada fallo clasificado como del buscador o del agente, y las
respuestas en bruto (respuestas/<caso>.json). Para que la suite rápida
las reproduzca, se copian a mano a tests/fijos/continuidad/respuestas/
tras revisarlas.

Umbral de aceptación (docs/agente_continuidad.md, «Cómo se mide»): al
menos 13 de los 16 casos con contradicción detectados, como mucho 1 aviso
entre controles y trampas, y todos los avisos cumplen las garantías.
"""

import os
from dataclasses import asdict

import pytest

from banco import (CON_CONTRADICCION, MAXIMO_AVISOS_EN_LIMPIOS, MINIMO_DETECTADOS, OBRA,
                   SALIDA, SIN_CONTRADICCION, cargar_casos, cobertura, construir_caso,
                   escribir, garantias_incumplidas, grabar_respuesta, obra_del_banco,
                   obtener_respuesta, puntuar, resumen_banco, resumen_cobertura)
from continuidad import reunir_contexto, texto_fragmento, validar

pytestmark = [pytest.mark.db, pytest.mark.api]


@pytest.fixture(scope="module")
def resultados(bd, modelo, tmp_path_factory) -> list[dict]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.exit("--api necesita ANTHROPIC_API_KEY en el entorno")
    filas = []
    destino = tmp_path_factory.mktemp("banco") / "exports"
    with obra_del_banco(bd, modelo, destino) as con:
        for caso in cargar_casos():
            with con.cursor() as cur:
                pasaje, char = construir_caso(caso, texto_fragmento(cur, OBRA, caso["orden"]))
                contexto = reunir_contexto(cur, OBRA, pasaje, caso["orden"], char, modelo)
            fila = {"id": caso["id"], "categoria": caso["categoria"]}
            if caso["categoria"] in CON_CONTRADICCION:
                fila["cobertura"] = cobertura(caso, pasaje, contexto)
            try:
                respuesta, llamada = obtener_respuesta(caso["id"], contexto, pasaje)
            except Exception as e:     # JSON roto o error de la API: se informa
                filas.append({**fila, "error": repr(e), "llamada": True,
                              "resultado": {"avisos": 0},
                              "descartados": 0, "garantias": []})
                continue
            informe = validar(respuesta, contexto, pasaje)
            grabar_respuesta(caso, pasaje, contexto, respuesta, informe)
            filas.append({**fila, "llamada": llamada,
                          "resultado": puntuar(caso, contexto, informe),
                          "descartados": len(informe.descartados),
                          "garantias": garantias_incumplidas(informe, contexto, pasaje),
                          "informe": asdict(informe)})
    escribir("banco.json", filas)
    texto = resumen_banco(filas) + "\n\n" + resumen_cobertura(
        [f["cobertura"] for f in filas if "cobertura" in f])
    (SALIDA / "banco.txt").write_text(texto + "\n", encoding="utf-8")
    print(f"\n{texto}\n(detalle en {SALIDA})")
    return filas


def test_ninguna_llamada_falla(resultados):
    assert [r["id"] for r in resultados if r.get("error")] == []


def test_detecta_al_menos_13_de_16(resultados):
    con = [r for r in resultados if r["categoria"] in CON_CONTRADICCION]
    assert len(con) == 16
    assert sum(bool(r["resultado"].get("detectado")) for r in con) >= MINIMO_DETECTADOS


def test_como_mucho_un_aviso_en_controles_y_trampas(resultados):
    sin = [r for r in resultados if r["categoria"] in SIN_CONTRADICCION]
    assert len(sin) == 11
    assert sum(r["resultado"]["avisos"] for r in sin) <= MAXIMO_AVISOS_EN_LIMPIOS


def test_todos_los_avisos_cumplen_las_garantias(resultados):
    assert {r["id"]: r["garantias"] for r in resultados if r["garantias"]} == {}


def test_cada_fallo_clasificado_como_del_buscador_o_del_agente(resultados):
    for r in resultados:
        if r["categoria"] in CON_CONTRADICCION and not r.get("error") \
                and not r["resultado"]["detectado"]:
            assert r["resultado"]["fallo"] in {"buscador", "agente"}
            if r["resultado"]["fallo"] == "buscador":
                assert r["cobertura"]["motivo"]
