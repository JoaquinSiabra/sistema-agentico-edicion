"""Los epítetos genéricos no atribuyen hechos ajenos (recuperar.hechos_hasta).

hechos_hasta(entidad=...) busca los hechos que nombran a la entidad por
cualquiera de sus formas registradas. Si un epíteto que comparten varios
personajes («domador de caballos») queda como alias de uno, ese personaje
se lleva los hechos de los demás. Cada prueba lleva su control: la misma
consulta con el epíteto como alias SÍ devuelve el hecho ajeno.
"""

import json
import shutil

import psycopg2
import pytest

from calibrar_glosario import REGISTROS, retirar_formas_compartidas
from poblar_almacen import poblar
from recuperar import hechos_hasta

pytestmark = pytest.mark.db

# Hecho de prueba: menciona el epíteto, no a Héctor.
HECHO_AJENO = ("Cástor, domador de caballos, y Pólux, excelente púgil, "
               "no aparecían entre los caudillos aqueos.")


def leer(ruta):
    return json.loads(ruta.read_text(encoding="utf-8"))


def escribir(ruta, datos):
    ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")


def preparar(exports, destino, epiteto_como_alias: bool):
    """Copia de los exports de prueba como los deja hoy la fase 0b (sin
    formas compartidas), con «domador de caballos» como epíteto de Héctor
    (o, para el control, como alias suyo) y un hecho que solo lo menciona."""
    shutil.copytree(exports, destino)
    registros = {k: leer(destino / f) for k, f in REGISTROS.items()}
    registros, _ = retirar_formas_compartidas(registros)
    hector = next(e for e in registros["personajes"] if e["termino"] == "Héctor")
    if epiteto_como_alias:
        hector["alias"] = hector["alias"] + ["domador de caballos"]
    else:
        hector["epitetos"] = ["domador de caballos"]
    for clave, fichero in REGISTROS.items():
        escribir(destino / fichero, registros[clave])
    canon = leer(destino / "canon" / "0002.json")
    canon["hechos_canonicos"].append({"hecho": HECHO_AJENO, "momento_canonico": "prueba",
                                      "atribucion": {"tipo": "narrador_documental",
                                                     "personaje": None}})
    escribir(destino / "canon" / "0002.json", canon)
    return destino


@pytest.fixture(scope="module")
def con_epitetos(bd, modelo, exports, tmp_path_factory):
    """Dos obras más en la base de prueba: el epíteto bien guardado y,
    como control, el epíteto como alias. Se borran al terminar."""
    raiz = tmp_path_factory.mktemp("epitetos")
    con = psycopg2.connect(bd)
    obras = {"iliada_epitetos": False, "iliada_epiteto_alias": True}
    try:
        for slug, como_alias in obras.items():
            poblar(con, modelo, slug, preparar(exports, raiz / slug, como_alias))
        yield con
    finally:
        con.rollback()
        with con.cursor() as cur:
            cur.execute("DELETE FROM obras WHERE slug = ANY(%s)", (list(obras),))
        con.commit()
        con.close()


def textos(con, obra, entidad):
    with con.cursor() as cur:
        return [h.hecho for h in hechos_hasta(cur, obra, 2, entidad=entidad)]


def test_epiteto_no_atribuye_hechos_a_hector(con_epitetos):
    hector = textos(con_epitetos, "iliada_epitetos", "Héctor")
    assert hector                                   # sus hechos sí salen
    assert HECHO_AJENO not in hector
    # Control: con el epíteto como alias, el hecho ajeno se le atribuía.
    assert HECHO_AJENO in textos(con_epitetos, "iliada_epiteto_alias", "Héctor")


def test_el_epiteto_no_es_una_forma_de_busqueda(con_epitetos):
    assert textos(con_epitetos, "iliada_epitetos", "domador de caballos") == []


def test_epiteto_compartido_en_lo_grabado(con_epitetos, cur, obra):
    # Caso real de los Cantos I-II: «valiente en la pelea» era alias de
    # Diomedes y de Menelao, así que a Menelao le salían hechos de Diomedes.
    def de_diomedes(hechos):
        return [h for h in hechos if "Diomedes, valiente en la pelea" in h]
    assert de_diomedes(textos(con_epitetos, "iliada_epitetos", "Diomedes"))   # control
    assert not de_diomedes(textos(con_epitetos, "iliada_epitetos", "Menelao"))
    # Control: el glosario grabado, sin sanear, sí se los atribuía.
    assert de_diomedes([h.hecho for h in hechos_hasta(cur, obra, 2, entidad="Menelao")])
