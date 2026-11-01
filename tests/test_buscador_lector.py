"""Buscador del lector (recuperar.consultar y el límite de buscador.py),
sin base de datos.

Lo que demuestra que no se filtra nada posterior es la prueba con base de
datos (tests/lector/). Aquí se comprueba que el límite va en el WHERE de
cada vía, que sin límite las consultas son las de siempre, y cómo consultar
combina vías y pasajes.
"""

import pytest

import buscador
import recuperar
from recuperar import Hecho, consultar, ultimo_fragmento_leido


class CursorFalso:
    def __init__(self, filas=()):
        self.consultas = []
        self.filas = list(filas)

    def execute(self, sql, params):
        self.consultas.append((sql, list(params)))

    def fetchall(self):
        return self.filas


FILA = (3, 45, "La espada se rompió.", "verso 361", "narrador_documental", None)


# --- El límite, en el WHERE --------------------------------------------------

def test_hechos_de_entidad_con_limite_filtra_en_la_consulta():
    cur = CursorFalso([FILA])
    buscador.hechos_de_entidad(cur, "iliada", "Menelao", hasta=2)
    [(sql, params)] = cur.consultas
    assert "AND f.orden_fragmento <= %s" in sql
    assert params == ["iliada", "Menelao", "Menelao", 2]
    assert sql.count("%s") == len(params)


def test_hechos_de_entidad_sin_limite_es_la_consulta_de_siempre():
    cur = CursorFalso()
    buscador.hechos_de_entidad(cur, "iliada", "Menelao")
    [(sql, params)] = cur.consultas
    assert "orden_fragmento <=" not in sql
    assert params == ["iliada", "Menelao", "Menelao"]


def test_via_lexica_con_limite_filtra_en_la_consulta():
    cur = CursorFalso([FILA])
    buscador.via_lexica(cur, "iliada", "La espada rota.", 3, "", 0, hasta=2)
    [(sql, params)] = cur.consultas
    assert "@@ q AND f.orden_fragmento <= %s" in sql
    assert params == ["la | espada | rota", "iliada", 2, buscador.MAXIMO_LEXICA]
    assert sql.count("%s") == len(params)


def test_via_lexica_sin_limite_es_la_consulta_de_siempre():
    cur = CursorFalso()
    buscador.via_lexica(cur, "iliada", "La espada rota.", 3, "", 0)
    [(sql, params)] = cur.consultas
    assert "orden_fragmento <=" not in sql
    assert params == ["la | espada | rota", "iliada", buscador.MAXIMO_LEXICA]


def test_las_vias_de_entidades_pasan_el_limite(monkeypatch):
    recibido = []
    monkeypatch.setattr(buscador, "entidades_de_obra",
                        lambda cur, obra: [{"termino": "Menelao", "alias": [], "variantes": []}])
    monkeypatch.setattr(buscador, "hechos_de_entidad",
                        lambda cur, obra, t, hasta=None: recibido.append(hasta) or [])
    buscador.via_entidades(None, "iliada", "Menelao.", 3, "", 0, hasta=2)
    texto = "Habló Menelao.\n\n"
    buscador.via_entorno(None, "iliada", "Y calló.", 3, texto, len(texto), hasta=2)
    assert recibido == [2, 2]


# --- Punto de lectura --------------------------------------------------------

@pytest.mark.parametrize("hasta_fragmento, hasta_char, ultimo", [
    (3, None, 3),    # fragmento leído entero
    (3, 5000, 2),    # lectura parcial: el fragmento en curso no aporta hechos
    (3, 0, 2),
])
def test_ultimo_fragmento_cuyos_hechos_se_admiten(hasta_fragmento, hasta_char, ultimo):
    assert ultimo_fragmento_leido(hasta_fragmento, hasta_char) == ultimo


# --- consultar --------------------------------------------------------------

TEXTO = "1 Verso uno.\n\n2 Verso dos, con Menelao.\n"


@pytest.fixture
def vias_falsas(monkeypatch):
    """Sustituye el texto del fragmento, las vías y la búsqueda de pasajes,
    y registra con qué se llamaron."""
    registro = {"vias": [], "pasajes": []}
    monkeypatch.setattr(buscador, "texto_fragmento", lambda cur, obra, orden: TEXTO)

    def via(nombre, hechos):
        def llamada(cur, obra, consulta, orden, texto, char, hasta=None):
            registro["vias"].append((nombre, orden, char, hasta))
            return {nombre: hechos}
        return llamada

    monkeypatch.setattr(buscador, "VIAS", {
        "entidades": via("entidades", [Hecho(2, 5, "b", "m", "narrador_documental", None),
                                       Hecho(1, 3, "a", "m", "narrador_documental", None)]),
        "lexica": via("lexica", [Hecho(2, 5, "b", "m", "narrador_documental", None)]),
    })
    monkeypatch.setattr(recuperar, "buscar_pasajes",
                        lambda *args: registro["pasajes"].append(args) or ["pasaje"])
    return registro


def test_consultar_con_el_fragmento_leido_entero(vias_falsas):
    respuesta = consultar("cur", "iliada", "¿Y Menelao?", 3, vias=("entidades", "lexica"))
    assert vias_falsas["vias"] == [("entidades", 3, len(TEXTO), 3), ("lexica", 3, len(TEXTO), 3)]
    # Una sola respuesta: hechos fusionados, en orden de lectura.
    assert [h.id for h in respuesta.hechos] == [10003, 20005]
    assert [h.puesto for h in respuesta.hechos] == [2, 1]   # 20005 llega por dos vías
    assert respuesta.candidatos == {"entidades": 2, "lexica": 1} and respuesta.omitidos == 0
    assert respuesta.pasajes == [] and vias_falsas["pasajes"] == []   # sin modelo


def test_consultar_a_mitad_de_fragmento(vias_falsas):
    respuesta = consultar("cur", "iliada", "¿Y Menelao?", 3, hasta_char=5,
                          modelo_embeddings="modelo", vias=("entidades",), k_pasajes=7)
    # Los hechos, solo hasta el fragmento anterior; los pasajes, hasta el carácter.
    assert vias_falsas["vias"] == [("entidades", 3, 5, 2)]
    assert vias_falsas["pasajes"] == [("cur", "modelo", "iliada", "¿Y Menelao?", 3, 5, 7)]
    assert respuesta.pasajes == ["pasaje"]


def test_consultar_aplica_el_tope_despues_de_fusionar(vias_falsas):
    respuesta = consultar("cur", "iliada", "¿Y Menelao?", 3, tope=1)
    assert [h.id for h in respuesta.hechos] == [20005] and respuesta.omitidos == 1


def test_consultar_con_errores_de_la_peticion(vias_falsas):
    with pytest.raises(ValueError, match="Vías desconocidas"):
        consultar("cur", "iliada", "¿Y Menelao?", 3, vias=("semantica",))
    with pytest.raises(ValueError, match="hasta_char"):
        consultar("cur", "iliada", "¿Y Menelao?", 3, hasta_char=len(TEXTO) + 1)
