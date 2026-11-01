"""revisar.py: el informe para el autor, sin base de datos ni API.

Los dos agentes se sustituyen por funciones que devuelven informes reales
de cada módulo; aquí solo se prueba cómo se combinan.
"""

import json

import pytest

import continuidad
import revisar
import voz


def informe_continuidad(avisos=1):
    return continuidad.Informe(
        avisos=[continuidad.Aviso("la flecha seguía clavada", 40030, "Macaón arrancó la flecha.",
                                  "narrador", "estado", "contradiccion", "anterior", "Ya no.")] * avisos,
        notas=[], descartados=[], hechos_omitidos=0)


def informe_voz(avisos=1):
    return voz.Informe(avisos=[voz.Aviso("lexico", "anacronismo", "su reloj", "ruptura",
                                         "la raíz de «reloj» no aparece en la obra",
                                         palabra="reloj")] * avisos,
                       notas=[], llamadas=1)


@pytest.fixture
def llamadas(monkeypatch):
    """Sustituye evaluar en los dos módulos y registra con qué se llamó."""
    registro = []

    def falso(nombre, informe):
        def evaluar(*args, **kwargs):
            registro.append((nombre, args, kwargs))
            if isinstance(informe, Exception):
                raise informe
            return informe
        return evaluar

    def instalar(c=informe_continuidad(), v=informe_voz()):
        monkeypatch.setattr(continuidad, "evaluar", falso("continuidad", c))
        monkeypatch.setattr(voz, "evaluar", falso("voz", v))
        return registro
    return instalar


def test_pasa_el_pasaje_por_los_dos_agentes_en_orden(llamadas):
    registro = llamadas()
    revision = revisar.revisar("cur", "iliada", "Pasaje.", 4, 100, cliente="cliente",
                               modelo_embeddings="modelo")
    assert list(revision.informes) == ["continuidad", "voz"] and revision.errores == {}
    assert revision.avisos == 2
    assert registro == [
        ("continuidad", ("cur", "iliada", "Pasaje.", 4, 100, "modelo", "cliente"), {}),
        ("voz", ("cur", "iliada", "Pasaje.", 4, 100, "cliente"), {}),
    ]


def test_se_eligen_los_agentes(llamadas):
    registro = llamadas()
    revision = revisar.revisar("cur", "iliada", "Pasaje.", 4, 0, agentes=("voz",))
    assert list(revision.informes) == ["voz"] and [r[0] for r in registro] == ["voz"]


def test_agente_desconocido(llamadas):
    llamadas()
    with pytest.raises(ValueError, match="Agentes desconocidos"):
        revisar.revisar("cur", "iliada", "Pasaje.", 4, 0, agentes=("estructura",))


@pytest.mark.parametrize("error", [RuntimeError("API caída"),
                                   json.JSONDecodeError("JSON roto", "{", 0)])
def test_si_un_agente_falla_los_demas_siguen(llamadas, error):
    llamadas(c=error)
    revision = revisar.revisar("cur", "iliada", "Pasaje.", 4, 0)
    assert list(revision.informes) == ["voz"]
    assert type(error).__name__ in revision.errores["continuidad"]


def test_una_posicion_mal_dada_no_se_oculta(llamadas):
    llamadas(v=ValueError("El fragmento tiene 10 caracteres; char=99."))
    with pytest.raises(ValueError, match="char=99"):
        revisar.revisar("cur", "iliada", "Pasaje.", 4, 99)


def test_informe_impreso(llamadas, capsys):
    llamadas(c=RuntimeError("API caída"))
    revisar.imprimir(revisar.revisar("cur", "iliada", "Pasaje.", 4, 0))
    salida = capsys.readouterr().out
    assert salida.index("=== Continuidad ===") < salida.index("=== Voz y edición ===")
    assert "No se pudo evaluar: RuntimeError: API caída" in salida
    assert "AVISO (ruptura, lexico: anacronismo) «su reloj»" in salida
    assert "1 avisos en total; 1 agentes no se pudieron evaluar." in salida


def test_informe_en_json(llamadas):
    llamadas()
    datos = json.loads(json.dumps(revisar.revisar("cur", "iliada", "P.", 4, 0).a_dict()))
    assert datos["informes"]["continuidad"]["avisos"][0]["hecho"] == 40030
    assert datos["informes"]["voz"]["avisos"][0]["palabra"] == "reloj"
    assert datos["errores"] == {}
