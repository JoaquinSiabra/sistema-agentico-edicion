"""Filtro anti-spoiler de recuperar.py contra la base de datos de prueba.

Para un punto de lectura dado, ni buscar_pasajes ni hechos_hasta pueden
devolver nada posterior, aunque la consulta pida explícitamente episodios
del Canto II. Cada comprobación de fuga lleva su control sin límite, que
demuestra que la consulta SÍ encontraría el episodio si estuviera
permitido; sin él la prueba podría pasar sin probar nada.
"""

import psycopg2
import pytest

from recuperar import buscar_pasajes, hechos_hasta

pytestmark = pytest.mark.db

# Episodios del Canto II: lo que un lector del Canto I no debe ver.
CONSULTAS_CANTO_II = {
    "Sueño": "Júpiter envía un sueño engañoso a Agamenón mientras duerme",
    "Tersites": "Tersites insulta a Agamenón y Ulises lo golpea con el cetro",
    "beocios": "Catálogo de las naves: los beocios y sus caudillos",
    "Iris": "Iris avisa a los troyanos y Héctor reúne a los aliados",
}
consultas = pytest.mark.parametrize("consulta", CONSULTAS_CANTO_II.values(),
                                    ids=CONSULTAS_CANTO_II.keys())


@pytest.fixture(scope="module")
def corpus(bd):
    """Número de pasajes del Canto I, punto de corte a mitad del Canto II y
    número de pasajes del Canto II que terminan antes de ese corte."""
    con = psycopg2.connect(bd)
    with con, con.cursor() as cur:
        cur.execute("""SELECT f.orden_fragmento, length(f.texto), count(p.id)
                       FROM fragmentos f JOIN pasajes p ON p.fragmento_id = f.id
                       GROUP BY f.id""")
        info = {orden: (largo, n) for orden, largo, n in cur.fetchall()}
        corte = info[2][0] // 2
        cur.execute("""SELECT count(*) FROM pasajes p JOIN fragmentos f ON f.id = p.fragmento_id
                       WHERE f.orden_fragmento = 2 AND p.fin_char <= %s""", (corte,))
        leidos_2 = cur.fetchone()[0]
    con.close()
    return {"pasajes_1": info[1][1], "corte": corte, "leidos_2": leidos_2}


# --- Pasajes ---------------------------------------------------------------

@consultas
def test_control_sin_limite_la_consulta_llega_al_canto_ii(cur, modelo, obra, consulta):
    [mejor] = buscar_pasajes(cur, modelo, obra, consulta, hasta_fragmento=2, k=1)
    assert mejor.orden_fragmento == 2


@consultas
def test_lector_del_canto_i_no_ve_el_canto_ii(cur, modelo, obra, corpus, consulta):
    # k mayor que todo el Canto I: obliga a agotar lo permitido.
    filas = buscar_pasajes(cur, modelo, obra, consulta, hasta_fragmento=1,
                           k=corpus["pasajes_1"] + 10)
    assert {p.orden_fragmento for p in filas} == {1}
    # El índice HNSW no pierde resultados por el filtro (scan iterativo).
    assert len(filas) == corpus["pasajes_1"]


@consultas
def test_lector_a_mitad_del_canto_ii_no_ve_la_segunda_mitad(cur, modelo, obra, corpus, consulta):
    filas = buscar_pasajes(cur, modelo, obra, consulta, hasta_fragmento=2,
                           hasta_char=corpus["corte"], k=500)
    assert not [p for p in filas if p.orden_fragmento == 2 and p.fin_char > corpus["corte"]]
    assert len(filas) == corpus["pasajes_1"] + corpus["leidos_2"]


# --- Hechos ----------------------------------------------------------------

def test_hechos_hasta_el_canto_i(cur, obra):
    hechos = hechos_hasta(cur, obra, hasta_fragmento=1)
    assert hechos and {h.orden_fragmento for h in hechos} == {1}
    assert len(hechos_hasta(cur, obra, hasta_fragmento=2)) > len(hechos)   # control


def test_canto_en_curso_no_aporta_hechos(cur, obra, corpus):
    parcial = hechos_hasta(cur, obra, hasta_fragmento=2, hasta_char=corpus["corte"])
    assert parcial == hechos_hasta(cur, obra, hasta_fragmento=1)


def test_entidad_que_aparece_despues_no_tiene_hechos_antes(cur, obra):
    assert hechos_hasta(cur, obra, 1, entidad="Tersites") == []
    assert hechos_hasta(cur, obra, 2, entidad="Tersites")   # control


def test_entidad_por_alias(cur, obra):
    por_alias = hechos_hasta(cur, obra, 1, entidad="hija de Brises")
    assert por_alias and por_alias == hechos_hasta(cur, obra, 1, entidad="Briseida")


def test_entidad_presente_en_ambos_cantos_se_corta_en_el_punto(cur, obra):
    hasta_1 = hechos_hasta(cur, obra, 1, entidad="Aquiles")
    hasta_2 = hechos_hasta(cur, obra, 2, entidad="Aquiles")
    assert hasta_1 and {h.orden_fragmento for h in hasta_1} == {1}
    assert len(hasta_2) > len(hasta_1)
