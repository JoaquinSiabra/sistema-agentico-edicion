"""Banco de Continuidad sin base de datos ni API.

Que cada caso se construye sobre el texto del fragmento, que sus hechos
esperados existen en el canon grabado, que la puntuación clasifica los
fallos, y (S) que con las respuestas grabadas el informe se reproduce igual.
"""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from banco import (CON_CONTRADICCION, GLOSARIO, GRABADAS, RAIZ, cargar_casos,
                   construir_caso, esperados, hecho_id, motivo_fallo_buscador, puntuar)
from continuidad import Aviso, Contexto, Informe, combinar, validar
from recuperar import Hecho

TEXTOS = RAIZ / "tests" / "fijos" / "textos"
CANTOS = {3: "iii", 4: "iv"}
CASOS = cargar_casos()
por_caso = pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])


def texto(orden: int) -> str:
    return (TEXTOS / f"iliada_canto_{CANTOS[orden]}.txt").read_text(encoding="utf-8")


def canon(orden: int) -> list[dict]:
    ruta = GLOSARIO / "respuestas_canon" / f"{orden:04d}.json"
    return json.loads(ruta.read_text(encoding="utf-8"))["hechos_canonicos"]


def test_el_banco_tiene_los_casos_de_la_especificacion():
    categorias = [c["categoria"] for c in CASOS]
    assert {k: categorias.count(k) for k in set(categorias)} == {
        "sembrado": 12, "direccion": 4, "control": 6, "trampa": 5}


@por_caso
def test_cada_caso_se_construye_sobre_su_fragmento(caso):
    fragmento = texto(caso["orden"])
    pasaje, char = construir_caso(caso, fragmento)
    verso = caso.get("tras_verso", caso.get("verso"))
    if "tras_verso" in caso:
        assert pasaje == caso["texto"]
        # Al final del párrafo de ese verso: justo antes del salto de línea.
        inicio = fragmento.rindex("\n", 0, char) + 1
        assert fragmento[inicio:].startswith(f"{verso} ")
        assert fragmento[char:char + 1] in ("\n", "")
    else:
        assert pasaje.startswith(f"{verso} ") and fragmento[char:].startswith(pasaje)
        assert char == 0 or fragmento[char - 1] == "\n"


def test_verso_inexistente():
    with pytest.raises(ValueError, match="9999"):
        construir_caso({"id": "X", "orden": 3, "verso": 9999}, texto(3))


def test_referencia_a_hecho():
    assert hecho_id("4.21") == 40021 and hecho_id("3.5") == 30005


@por_caso
def test_los_hechos_esperados_existen_y_son_del_narrador(caso):
    for referencia in (r for e in caso["esperado"] for r in e["hechos"]):
        orden, indice = map(int, referencia.split("."))
        assert canon(orden)[indice]["atribucion"]["tipo"] == "narrador_documental"


# --- Puntuación y clasificación de fallos -----------------------------------

def contexto_con(*hechos) -> Contexto:
    lista, omitidos = combinar({"entidad: Menelao": list(hechos)}, orden=4)
    return Contexto(obra="x", orden=4, char=0, hechos=lista,
                    candidatos={"entidad: Menelao": len(hechos)}, hechos_omitidos=omitidos)


def informe_con(*ids) -> Informe:
    return Informe(avisos=[Aviso("c", i, "t", "narrador", "estado", "contradiccion",
                                 "anterior", "e") for i in ids],
                   notas=[], descartados=[], hechos_omitidos=0)


S12 = next(c for c in CASOS if c["id"] == "S12")      # espera 4.30
CONTROL = next(c for c in CASOS if c["categoria"] == "control")


def test_detectado_si_algun_aviso_cita_un_hecho_esperado():
    r = puntuar(S12, contexto_con(Hecho(4, 30, "t", "m", "narrador_documental", None)),
                informe_con(40030))
    assert r["detectado"] and r["gravedad_correcta"] and r["direccion_correcta"]


def test_fallo_del_agente_si_el_hecho_llego_y_no_lo_cito():
    r = puntuar(S12, contexto_con(Hecho(4, 30, "t", "m", "narrador_documental", None)),
                informe_con())
    assert not r["detectado"] and r["fallo"] == "agente"


def test_fallo_del_buscador_si_el_hecho_no_llego():
    r = puntuar(S12, contexto_con(Hecho(4, 1, "t", "m", "narrador_documental", None)),
                informe_con(40001))
    assert not r["detectado"] and r["fallo"] == "buscador"


def test_un_control_con_avisos_no_es_correcto():
    assert puntuar(CONTROL, contexto_con(), informe_con())["correcto"]
    assert not puntuar(CONTROL, contexto_con(), informe_con(30001))["correcto"]


def test_motivo_observable_de_cada_fallo_del_buscador():
    sin_nadie = Contexto(obra="x", orden=4, char=0, hechos=[], candidatos={}, hechos_omitidos=0)
    assert motivo_fallo_buscador("Y blandía la espada.", sin_nadie) == \
        "ninguna entidad detectada en el pasaje"
    assert "epíteto o una forma compartida" in motivo_fallo_buscador(
        "Llegó el domador de caballos.", sin_nadie)
    assert "revisar a mano" in motivo_fallo_buscador("Habló Menelao.", contexto_con())


# --- Respuestas grabadas del banco (S) ---------------------------------------

GRABADOS = sorted(GRABADAS.glob("*.json")) if GRABADAS.exists() else []


@pytest.mark.skipif(not GRABADOS, reason=f"aún no hay respuestas grabadas en {GRABADAS}")
@pytest.mark.parametrize("ruta", GRABADOS, ids=[r.stem for r in GRABADOS])
def test_con_respuestas_grabadas_el_informe_se_reproduce_igual(ruta: Path):
    grabado = json.loads(ruta.read_text(encoding="utf-8"))
    contexto = Contexto.desde_dict(grabado["contexto"])
    informe = validar(grabado["respuesta"], contexto, grabado["pasaje"])
    assert json.loads(json.dumps(asdict(informe))) == grabado["informe"]


def test_las_categorias_con_contradiccion_esperan_hechos():
    for caso in CASOS:
        assert bool(esperados(caso)) == (caso["categoria"] in CON_CONTRADICCION)
