"""Agente de Continuidad (continuidad.py) sin base de datos ni API.

Lo determinista del agente: detección de entidades, unión de las vías con
su origen y el tope, el prompt y la validación de lo que devuelve el
modelo. La llamada al modelo va con el ClienteFalso de conftest.py.
"""

import json
from pathlib import Path

import pytest

import calibrar_glosario
import continuidad
from continuidad import (Contexto, combinar, construir_prompt, consultar_modelo,
                         detectar_entidades, validar)
from recuperar import Hecho, Pasaje

RAIZ = Path(__file__).resolve().parents[1]
FIJOS = RAIZ / "tests" / "fijos" / "iliada_cantos_iii_iv"


def glosario_tras_canto_iv() -> list[dict]:
    return [e for nombre in ("personajes_obra.json", "lugares_obra.json", "glosario_obra.json")
            for e in json.loads((FIJOS / nombre).read_text(encoding="utf-8"))]


def hecho(orden, indice, texto="Un hecho.", tipo="narrador_documental", quien=None):
    return Hecho(orden, indice, texto, "en algún momento", tipo, quien)


PASAJE = "Menelao,   caro á Marte, arremetió el primero contra los teucros."


def contexto(orden=4) -> Contexto:
    """Tres hechos: uno anterior del narrador, uno del mismo fragmento del
    narrador y uno posterior que afirma un personaje."""
    hechos, omitidos = combinar({"entidad: Menelao": [
        hecho(3, 44, "Menelao venció a Alejandro."),
        hecho(4, 30, "Macaón arrancó la flecha del cinturón de Menelao."),
        hecho(5, 2, "Menelao ha muerto.", "afirmacion_interesada", "Héctor"),
    ]}, orden)
    return Contexto(obra="iliada", orden=orden, char=100, hechos=hechos,
                    candidatos={"entidad: Menelao": 3}, hechos_omitidos=omitidos,
                    pasajes=[Pasaje(3, 7, 0, 50, "Texto de un pasaje parecido.", 0.9)],
                    entorno_antes="…lo que hay antes.", entorno_despues="Lo que hay después…")


def aviso(**cambios) -> dict:
    return {"cita": "arremetió el primero", "hecho": 30044, "tipo": "estado",
            "gravedad": "contradiccion", "direccion": "anterior",
            "explicacion": "No cuadra.", **cambios}


# --- Entidades del pasaje ----------------------------------------------------

ENTIDADES = [
    {"termino": "Agamenón", "alias": ["Atrida Agamenón"], "variantes": ["rey de hombres"]},
    {"termino": "Ida", "alias": [], "variantes": []},
    {"termino": "Cástor", "alias": [], "variantes": [], "epitetos": ["domador de caballos"]},
]


@pytest.mark.parametrize("pasaje", [
    "Habló Agamenón.", "habló agamenón.", "Lo dijo el Atrida Agamenón.",
    "Llegó el REY DE HOMBRES con su lanza.",
])
def test_entidad_por_termino_alias_o_variante_sin_mayusculas(pasaje):
    assert detectar_entidades(pasaje, ENTIDADES) == ["Agamenón"]


def test_entidad_solo_como_palabra_completa():
    assert detectar_entidades("Idomeneo y los idos.", ENTIDADES) == []
    assert detectar_entidades("Bajaron del Ida.", ENTIDADES) == ["Ida"]   # control


def test_variante_de_la_obra():
    assert detectar_entidades("Llegaron los teucros.", glosario_tras_canto_iv()) == ["troyanos"]


def test_epiteto_o_forma_compartida_no_trae_entidades():
    glosario = glosario_tras_canto_iv()
    # «domador de caballos» es epíteto de Cástor; «valiente en la pelea» y
    # «famoso por su lanza», formas compartidas ya retiradas de los alias.
    pasaje = "Llegó el domador de caballos, valiente en la pelea, famoso por su lanza."
    assert detectar_entidades(pasaje, glosario) == []
    assert detectar_entidades(pasaje, ENTIDADES) == []
    assert detectar_entidades("Llegó Cástor.", glosario) == ["Cástor"]     # control


# --- Combinación de vías, origen y tope -------------------------------------

def test_cada_hecho_indica_por_que_via_llego():
    comun = hecho(3, 1)
    hechos, _ = combinar({"entidad: Helena": [comun, hecho(3, 2)],
                          "entidad: Menelao": [comun]}, orden=4)
    assert {h.id: h.origen for h in hechos} == {
        30001: ["entidad: Helena", "entidad: Menelao"],
        30002: ["entidad: Helena"],
    }


def test_direccion_respecto_al_punto_de_insercion():
    hechos, _ = combinar({"entidad: X": [hecho(3, 1), hecho(4, 1), hecho(5, 1)]}, orden=4)
    assert [h.direccion for h in hechos] == ["anterior", "mismo_fragmento", "posterior"]


def test_con_mas_hechos_que_el_tope_se_omiten_los_de_las_entidades_mas_generales():
    especifica = [hecho(3, i) for i in range(3)]
    general = [hecho(4, i) for i in range(10)]
    hechos, omitidos = combinar({"entidad: aqueos": general,
                                 "entidad: Macaón": especifica}, orden=4, tope=5)
    ids = [h.id for h in hechos]
    assert len(ids) == 5 and omitidos == 8
    assert {30000, 30001, 30002} <= set(ids)          # la específica, entera
    ctx = Contexto(obra="iliada", orden=4, char=0, hechos=hechos, candidatos={},
                   hechos_omitidos=omitidos)
    assert validar({}, ctx, "").hechos_omitidos == 8   # y el informe lo dice


def test_sin_pasar_el_tope_no_se_omite_nada():
    hechos, omitidos = combinar({"entidad: X": [hecho(3, i) for i in range(5)]}, orden=4, tope=5)
    assert len(hechos) == 5 and omitidos == 0


def test_via_desconocida():
    with pytest.raises(ValueError, match="Vías desconocidas"):
        continuidad.reunir_contexto(None, "iliada", "", 1, 0, vias=("lexica",))


# --- Prompt ------------------------------------------------------------------

def test_el_prompt_lleva_cada_hecho_con_identificador_atribucion_y_direccion():
    ctx = contexto()
    sistema, usuario = construir_prompt(ctx, PASAJE)
    assert sistema == continuidad.PROMPT_SISTEMA
    lineas = {h.id: next(l for l in usuario.splitlines() if l.startswith(f"[{h.id}]"))
              for h in ctx.hechos}
    for h in ctx.hechos:
        assert h.atribucion_tipo in lineas[h.id] and h.direccion in lineas[h.id]
        assert h.hecho in lineas[h.id]
    assert "NARRADOR" in lineas[30044]
    assert "PERSONAJE: Héctor" in lineas[50002] and "NARRADOR" not in lineas[50002]


def test_el_prompt_lleva_el_pasaje_el_entorno_y_los_pasajes_como_contexto():
    _, usuario = construir_prompt(contexto(), PASAJE)
    assert PASAJE in usuario
    assert "…lo que hay antes." in usuario and "Lo que hay después…" in usuario
    assert "no se citan como prueba" in usuario and "Texto de un pasaje parecido." in usuario
    assert usuario.index("PASAJE NUEVO") > usuario.index("HECHOS CANÓNICOS")


def test_el_prompt_de_sistema_explica_las_reglas():
    for regla in ("NARRADOR", "notas", '"contradiccion"', '"tension"', "LITERAL",
                  "listas vacías"):
        assert regla in continuidad.PROMPT_SISTEMA


# --- Validar -----------------------------------------------------------------

def test_aviso_valido_lleva_el_texto_del_hecho_y_quien_lo_afirma():
    informe = validar({"avisos": [aviso()]}, contexto(), PASAJE)
    [a] = informe.avisos
    assert (a.hecho, a.texto_hecho, a.afirmado_por) == (30044, "Menelao venció a Alejandro.",
                                                       "narrador")
    assert informe.descartados == [] and informe.notas == []


def test_cita_con_otros_espacios_vale():
    informe = validar({"avisos": [aviso(cita="Menelao, caro á\nMarte")]}, contexto(), PASAJE)
    assert len(informe.avisos) == 1


@pytest.mark.parametrize("cita", ["arremetió el último", "", None, "MENELAO, CARO Á MARTE"])
def test_aviso_con_cita_no_literal_se_descarta(cita):
    informe = validar({"avisos": [aviso(cita=cita)]}, contexto(), PASAJE)
    assert informe.avisos == []
    assert [d["motivo"] for d in informe.descartados] == ["cita no literal"]


@pytest.mark.parametrize("identificador", [99999, None, "no es un número"])
def test_aviso_con_hecho_fuera_del_contexto_se_descarta(identificador):
    informe = validar({"avisos": [aviso(hecho=identificador)]}, contexto(), PASAJE)
    assert informe.avisos == []
    assert [d["motivo"] for d in informe.descartados] == ["hecho fuera del contexto"]


def test_aviso_apoyado_en_un_hecho_de_personaje_pasa_a_notas():
    informe = validar({"avisos": [aviso(hecho=50002, direccion="posterior")]}, contexto(), PASAJE)
    assert informe.avisos == [] and informe.descartados == []
    [nota] = informe.notas
    assert (nota.hecho, nota.afirmado_por, nota.cita) == (50002, "Héctor", "arremetió el primero")


@pytest.mark.parametrize("dicho", ["posterior", "anterior", "mismo_fragmento", "hacia atrás", None])
def test_entre_fragmentos_la_direccion_la_fija_el_codigo(dicho):
    informe = validar({"avisos": [aviso(direccion=dicho)]}, contexto(), PASAJE)
    assert [a.direccion for a in informe.avisos] == ["anterior"]


@pytest.mark.parametrize("dicho", ["anterior", "posterior"])
def test_en_el_mismo_fragmento_la_direccion_la_decide_el_modelo(dicho):
    informe = validar({"avisos": [aviso(hecho=40030, direccion=dicho)]}, contexto(), PASAJE)
    assert [a.direccion for a in informe.avisos] == [dicho]


@pytest.mark.parametrize("cambio", [
    {"tipo": "caracterizacion"}, {"gravedad": "grave"}, {"tipo": None},
    {"hecho": 40030, "direccion": "mismo_fragmento"}, {"hecho": 40030, "direccion": None},
])
def test_valores_no_permitidos_descartan_el_aviso(cambio):
    informe = validar({"avisos": [aviso(**cambio)]}, contexto(), PASAJE)
    assert informe.avisos == []
    assert [d["motivo"] for d in informe.descartados] == ["valor no permitido"]


def test_todo_lo_descartado_se_cuenta():
    respuesta = {"avisos": [aviso(), aviso(cita="inventada"), aviso(hecho=1), "basura",
                            aviso(gravedad="leve")]}
    informe = validar(respuesta, contexto(), PASAJE)
    assert len(informe.avisos) == 1 and len(informe.descartados) == 4


def test_respuesta_vacia_no_da_avisos():
    for respuesta in ({}, {"avisos": [], "notas": []}, {"avisos": None}):
        informe = validar(respuesta, contexto(), PASAJE)
        assert informe.avisos == informe.notas == informe.descartados == []


def test_las_notas_del_modelo_se_conservan_con_su_hecho():
    respuesta = {"notas": [{"cita": "Menelao", "hecho": 50002, "explicacion": "Lo dice Héctor."},
                           {"cita": "teucros", "hecho": None, "explicacion": "Sin hecho."}]}
    notas = validar(respuesta, contexto(), PASAJE).notas
    assert [(n.hecho, n.texto_hecho, n.afirmado_por) for n in notas] == [
        (50002, "Menelao ha muerto.", "Héctor"), (None, None, None)]


# --- Consultar al modelo (API simulada) -------------------------------------

def test_consulta_una_llamada_con_el_modelo_del_resto_de_fases(api):
    respuesta = {"avisos": [aviso()], "notas": []}
    cliente = api([json.dumps(respuesta)])
    assert consultar_modelo(contexto(), PASAJE) == respuesta
    [peticion] = cliente.peticiones
    assert peticion["model"] == calibrar_glosario.MODELO
    sistema, usuario = construir_prompt(contexto(), PASAJE)
    assert peticion["system"] == sistema
    assert peticion["messages"] == [{"role": "user", "content": usuario}]


def test_consulta_con_cliente_explicito_y_json_envuelto(api):
    cliente = api(['```json\n{"avisos": [], "notas": []}\n```'])
    assert consultar_modelo(contexto(), PASAJE, cliente=cliente) == {"avisos": [], "notas": []}


@pytest.mark.parametrize("cierre", ["\n```", "\n```\n", ""])
def test_consulta_con_razonamiento_antes_del_json(api, cierre):
    # Caso real del banco (S03, C02): prosa primero y el JSON al final.
    api(["Analizando el pasaje nuevo contra los hechos canónicos:\n\n"
         "- [40030]: Macaón trató la herida.\n\nNo hay contradicción.\n\n"
         '```json\n{"avisos": [], "notas": []}' + cierre])
    assert consultar_modelo(contexto(), PASAJE) == {"avisos": [], "notas": []}


def test_consulta_con_json_roto_falla(api):
    api(["{no es json"])
    with pytest.raises(json.JSONDecodeError):
        consultar_modelo(contexto(), PASAJE)


def test_el_contexto_se_puede_guardar_y_recuperar():
    from dataclasses import asdict
    ctx = contexto()
    assert Contexto.desde_dict(json.loads(json.dumps(asdict(ctx)))) == ctx
