"""reunir_contexto contra la base de datos de prueba (Cantos I-IV).

Detección de entidades, dirección de los hechos, mismo criterio que
recuperar.hechos_hasta y, sin modelo ni coste, la cobertura del contexto
en el banco: en cada caso con contradicción, ¿llegó algún hecho esperado,
y por qué vía? La cobertura es informativa (sin umbral en v1): se escribe
en .cache/continuidad/cobertura.json y se imprime (ver con -s).
"""

import pytest

from banco import (CON_CONTRADICCION, OBRA, cargar_casos, cobertura, construir_caso,
                   escribir, obra_del_banco, resumen_cobertura)
from continuidad import reunir_contexto, texto_fragmento
from recuperar import hechos_hasta

pytestmark = pytest.mark.db


@pytest.fixture(scope="module", autouse=True)
def obra_cargada(bd, modelo, tmp_path_factory):
    with obra_del_banco(bd, modelo, tmp_path_factory.mktemp("continuidad") / "exports"):
        yield


def contexto(cur, pasaje, orden=4, char=0, **opciones):
    return reunir_contexto(cur, OBRA, pasaje, orden, char, **opciones)


def test_epiteto_o_forma_compartida_no_trae_entidades(cur):
    ctx = contexto(cur, "Llegó el domador de caballos, valiente en la pelea.")
    assert ctx.candidatos == {} and ctx.hechos == []
    control = contexto(cur, "Llegó Cástor.")
    assert "entidad: Cástor" in control.candidatos
    assert 30030 in {h.id for h in control.hechos}     # Cástor y Pólux ya estaban muertos


def test_el_contexto_incluye_hechos_posteriores_con_su_direccion(cur):
    # Desde el Canto III: Macaón sale en el catálogo del II y cura a
    # Menelao en el IV.
    macaon = {h.id: h.direccion for h in contexto(cur, "Llegó Macaón.", orden=3).hechos}
    assert macaon[40030] == "posterior"
    assert {d for i, d in macaon.items() if i < 30000} == {"anterior"}
    castor_en_iv = {h.id: h.direccion for h in contexto(cur, "Cástor.", orden=4).hechos}
    castor_en_iii = {h.id: h.direccion for h in contexto(cur, "Cástor.", orden=3).hechos}
    assert castor_en_iv[30030] == "anterior"
    assert castor_en_iii[30030] == "mismo_fragmento"


def test_entre_fragmentos_la_direccion_es_correcta(cur):
    for orden in (1, 2, 3, 4):
        for h in contexto(cur, "Menelao y Agamenón.", orden=orden).hechos:
            esperada = ("anterior" if h.orden_fragmento < orden else
                        "posterior" if h.orden_fragmento > orden else "mismo_fragmento")
            assert h.direccion == esperada


def test_mismo_criterio_que_hechos_hasta_sin_limite_de_posicion(cur):
    ctx = contexto(cur, "Pándaro hirió al rey de hombres Agamenón.", tope=10**6)
    assert set(ctx.candidatos) == {"entidad: Agamenón", "entidad: Pándaro"}
    for origen, n in ctx.candidatos.items():
        termino = origen.removeprefix("entidad: ")
        esperados = {h.orden_fragmento * 10000 + h.orden_en_fragmento
                     for h in hechos_hasta(cur, OBRA, 4, entidad=termino)}
        assert {h.id for h in ctx.hechos if origen in h.origen} == esperados
        assert n == len(esperados)


def test_cada_hecho_indica_por_que_via_llego(cur):
    ctx = contexto(cur, "Menelao, Helena y los aqueos.")
    assert ctx.hechos
    assert all(h.origen and all(o in ctx.candidatos for o in h.origen) for h in ctx.hechos)


def test_el_tope_omite_y_lo_informa(cur):
    ctx = contexto(cur, "Los aqueos y los troyanos.", tope=20)
    assert len(ctx.hechos) == 20 and ctx.hechos_omitidos > 0


def test_pasajes_parecidos_de_toda_la_obra_y_entorno(cur, modelo):
    texto = texto_fragmento(cur, OBRA, 1)
    ctx = contexto(cur, "Pándaro dispara una flecha contra Menelao y rompe los juramentos.",
                   orden=1, char=3000, modelo_embeddings=modelo)
    assert len(ctx.pasajes) == 5
    assert any(p.orden_fragmento == 4 for p in ctx.pasajes)     # posterior al punto
    assert ctx.entorno_antes == texto[1500:3000] and ctx.entorno_despues == texto[3000:4500]


def test_posicion_fuera_del_fragmento(cur):
    with pytest.raises(ValueError):
        contexto(cur, "Menelao.", orden=9)
    with pytest.raises(ValueError):
        contexto(cur, "Menelao.", orden=4, char=10**7)


def test_cobertura_del_contexto_en_el_banco(cur):
    filas = []
    for caso in cargar_casos():
        if caso["categoria"] not in CON_CONTRADICCION:
            continue
        pasaje, char = construir_caso(caso, texto_fragmento(cur, OBRA, caso["orden"]))
        filas.append(cobertura(caso, pasaje, contexto(cur, pasaje, caso["orden"], char)))
    ruta = escribir("cobertura.json", filas)
    print("\n" + resumen_cobertura(filas) + f"\n(detalle en {ruta})")
    assert len(filas) == 16
    assert all(f["llegaron"] or f["motivo"] for f in filas)
