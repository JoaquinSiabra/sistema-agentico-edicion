"""Buscador del lector contra la base de datos de prueba (Cantos I-IV).

La garantía (nada posterior al punto de lectura, ni hechos ni pasajes), las
fugas de cada spoiler, el control de cada uno y el umbral de cobertura. El
informe por caso se escribe en .cache/lector/buscador.txt y se imprime
(ver con -s). Sin API: el buscador no llama al modelo.
"""

import pytest

from banco_lector import (CUBIERTAS, OBRA, cargar_casos, control_de, escribir, hecho_id,
                          medir, obra_del_banco, punto, resumen, texto)
from recuperar import consultar

pytestmark = pytest.mark.db

CASOS = cargar_casos()


@pytest.fixture(scope="module")
def respuestas(bd, modelo, tmp_path_factory):
    with obra_del_banco(bd, modelo, tmp_path_factory.mktemp("lector") / "exports") as con:
        with con.cursor() as cur:
            yield {c["id"]: consultar(cur, OBRA, c["consulta"], *punto(c, texto(c["punto"]["orden"])),
                                      modelo_embeddings=modelo)
                   for c in CASOS}


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_nada_posterior_al_punto_de_lectura(caso, respuestas):
    hasta_fragmento, hasta_char = punto(caso, texto(caso["punto"]["orden"]))
    r = respuestas[caso["id"]]
    ultimo = hasta_fragmento if hasta_char is None else hasta_fragmento - 1
    assert all(h.orden_fragmento <= ultimo for h in r.hechos)
    assert all(p.orden_fragmento < hasta_fragmento
               or (p.orden_fragmento == hasta_fragmento
                   and (hasta_char is None or p.fin_char <= hasta_char))
               for p in r.pasajes)


@pytest.mark.parametrize("caso", [c for c in CASOS if c.get("fugas")], ids=lambda c: c["id"])
def test_ninguna_fuga_y_el_control_si_las_encuentra(caso, respuestas):
    fugas = {hecho_id(r) for r in caso["fugas"]}
    assert not fugas & {h.id for h in respuestas[caso["id"]].hechos}
    # Sin el control, que no llegue nada podría no probar nada.
    control = control_de(caso, CASOS)
    assert fugas & {h.id for h in respuestas[control["id"]].hechos}


def test_informe_y_umbral_de_cobertura(respuestas):
    filas = [medir(c, respuestas[c["id"]]) for c in CASOS]
    ruta = escribir(filas)
    print("\n" + resumen(filas) + f"\n(en {ruta})")
    no_cubiertas = [f["id"] for f in filas if f["categoria"] in CUBIERTAS and not f["cubierto"]]
    assert no_cubiertas == []
