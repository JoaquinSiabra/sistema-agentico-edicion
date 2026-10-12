"""Filtro anti-spoiler de recuperar.py, sin base de datos.

Lo que de verdad demuestra que no se filtra nada posterior es la prueba
con base de datos (data/iliada/prueba_antispoiler.py). Aquí solo se
comprueba que la condición se construye bien.
"""

from recuperar import _filtro_pasajes


def test_fragmento_completo_no_mira_caracteres():
    sql, params = _filtro_pasajes(2, None)
    assert "fin_char" not in sql
    assert params == [2]


def test_lectura_parcial_corta_por_caracter_solo_en_el_fragmento_en_curso():
    sql, params = _filtro_pasajes(2, 5000)
    assert "f.orden_fragmento < %s" in sql
    assert "f.orden_fragmento = %s AND p.fin_char <= %s" in sql
    assert params == [2, 2, 5000]


def test_cada_marcador_tiene_su_parametro():
    for hasta_char in (None, 0, 5000):
        sql, params = _filtro_pasajes(3, hasta_char)
        assert sql.count("%s") == len(params)
