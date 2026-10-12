"""Troceado de fragmentos en pasajes y rutas de origen (poblar_almacen.py).

Con un tokenizador falso (una palabra = un token) para no cargar e5: lo
que se prueba es la lógica de agrupar frases, no el tokenizador real.
"""

import re

from poblar_almacen import TOKENS_POR_PASAJE, TOKENS_SOLAPE, _frases, resolver_ruta, trocear


class ModeloFalso:
    @staticmethod
    def tokenizer(texto, add_special_tokens=False):
        return {"input_ids": texto.split()}


def n_tokens(texto: str) -> int:
    return len(texto.split())


def texto_de_frases(n_frases: int, palabras_por_frase: int) -> str:
    return " ".join(
        " ".join(f"p{i}_{j}" for j in range(palabras_por_frase)) + "."
        for i in range(n_frases)
    )


# --- _frases -------------------------------------------------------------

def test_frases_corta_tras_puntuacion_y_comillas_de_cierre():
    texto = "Canta, oh diosa. ¿Quién los enfrentó? «Vete», dijo él.» Fin"
    frases = [texto[a:b] for a, b in _frases(texto)]
    assert frases == ["Canta, oh diosa.", "¿Quién los enfrentó?", "«Vete», dijo él.»", "Fin"]


def test_frases_corta_en_salto_de_linea_y_sin_espacios_en_los_bordes():
    texto = "  verso uno\n\n  verso dos  \n"
    assert [texto[a:b] for a, b in _frases(texto)] == ["verso uno", "verso dos"]


def test_frases_no_corta_un_punto_sin_espacio_detras():
    texto = "El año 1.200 a.C. llegaron"
    # "a.C. " sí corta (punto seguido de espacio); "1.200" no.
    assert [texto[a:b] for a, b in _frases(texto)] == ["El año 1.200 a.C.", "llegaron"]


# --- trocear -------------------------------------------------------------

def test_texto_corto_es_un_solo_pasaje():
    texto = "  Canta, oh diosa, la cólera del Pelida Aquiles.  "
    assert trocear(ModeloFalso, texto) == [(2, len(texto) - 2)]


def test_ningun_pasaje_supera_el_limite():
    texto = texto_de_frases(200, 17)
    for a, b in trocear(ModeloFalso, texto):
        assert n_tokens(texto[a:b]) <= TOKENS_POR_PASAJE


def test_los_pasajes_cubren_todo_el_texto_en_orden():
    texto = texto_de_frases(200, 17)
    pasajes = trocear(ModeloFalso, texto)
    assert pasajes[0][0] == 0 and pasajes[-1][1] == len(texto)
    for (a1, b1), (a2, b2) in zip(pasajes, pasajes[1:]):
        assert a1 < a2 and b1 < b2      # avanzan
        assert a2 <= b1 + 1             # sin huecos (como mucho el espacio)


def test_solape_entre_pasajes_contiguos_respeta_el_maximo():
    texto = texto_de_frases(200, 17)
    pasajes = trocear(ModeloFalso, texto)
    for (_, b1), (a2, _) in zip(pasajes, pasajes[1:]):
        solape = texto[a2:b1] if a2 < b1 else ""
        assert 0 < n_tokens(solape) <= TOKENS_SOLAPE


def test_frase_gigante_se_parte_por_palabras():
    # Catálogo sin puntos: una sola "frase" de 1000 palabras.
    texto = " ".join(f"nave{i}" for i in range(1000))
    pasajes = trocear(ModeloFalso, texto)
    assert len(pasajes) >= 3
    palabras_cubiertas = set()
    for a, b in pasajes:
        assert n_tokens(texto[a:b]) <= TOKENS_POR_PASAJE
        palabras_cubiertas |= set(texto[a:b].split())
    assert palabras_cubiertas == set(texto.split())


def test_spans_no_parten_palabras():
    texto = texto_de_frases(120, 23)
    for a, b in trocear(ModeloFalso, texto):
        assert a == 0 or texto[a - 1].isspace()
        assert b == len(texto) or texto[b].isspace()
        assert re.fullmatch(r"\S.*\S", texto[a:b], re.S)


# --- resolver_ruta -------------------------------------------------------

def test_ruta_que_existe_se_usa_tal_cual(tmp_path):
    fichero = tmp_path / "canto.txt"
    fichero.write_text("x", encoding="utf-8")
    assert resolver_ruta(str(fichero)) == fichero


def test_ruta_de_windows_se_traduce_a_wsl_y_viceversa():
    assert resolver_ruta(r"Q:\no\existe\canto.txt").as_posix() == "/mnt/q/no/existe/canto.txt"
    assert resolver_ruta("/mnt/q/no/existe/canto.txt").as_posix() == "Q:/no/existe/canto.txt"
