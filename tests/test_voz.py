"""Agente de Voz y edición (voz.py) sin base de datos ni API.

Todo lo determinista del agente: el perfil de la obra (normas y raíces),
la medida del pasaje (normas rotas y candidatos léxicos), el prompt y la
validación. La llamada al modelo va con el ClienteFalso de conftest.py.
"""

import json
from pathlib import Path

import pytest

import calibrar_glosario
import voz
from voz import (Aviso, Entorno, Medida, construir_prompt, consultar_modelo,
                 candidatos_lexicos, evaluar_en, limpiar, medir, normas_de,
                 perfil_de_textos, validar)

RAIZ = Path(__file__).resolve().parents[1]
TEXTOS = [RAIZ / "tests" / "fijos" / "textos" / f"iliada_canto_{c}.txt"
          for c in ("i", "ii", "iii", "iv")]


@pytest.fixture(scope="module")
def iliada():
    return perfil_de_textos([t.read_text(encoding="utf-8") for t in TEXTOS])


def normas(perfil):
    return {(n.forma_obra, n.forma_rota) for n in perfil.normas}


# --- Perfil -----------------------------------------------------------------

def test_limpiar_quita_numeros_titulos_y_pies_de_ilustracion():
    versos = [f"{n} Verso {n}." for n in range(2, 9)]
    texto = ("CANTO III\nJURAMENTOS\n\n1 Avanzaban los teucros.\n\n" + "\n\n".join(versos) +
             "\n\n111 Tal dijo. Gozáronse aqueos y teucros, y colocaron en\n\n"
             "Paris desafiaba á los argivos\n(Canto III, versos 18 á 20.)\n"
             "filas los carros.\n")
    assert limpiar(texto).splitlines() == [
        "Avanzaban los teucros.", *(f"Verso {n}." for n in range(2, 9)),
        "Tal dijo. Gozáronse aqueos y teucros, y colocaron en", "filas los carros."]


def test_una_obra_sin_numeracion_se_deja_tal_cual():
    texto = "Capítulo 1\n\nEra una noche oscura.\n\n2 de mayo: llovía."
    assert limpiar(texto) == texto


def test_normas_de_la_iliada(iliada):
    assert normas(iliada) == {
        ("á", "a"), ("ó", "o"), ("é", "e"), ("fué", "fue"), ("dió", "dio"),
        ("comillas angulares", "raya de diálogo"), ("comillas angulares", "comillas inglesas")}
    # «vió/vio» solo aparece 3 veces: no llega al mínimo.
    assert not [n for n in iliada.normas if "vio" in (n.forma_obra, n.forma_rota)]


def test_la_norma_sale_en_la_direccion_que_usa_la_obra():
    moderno = "Fue a casa o al campo, y dio un paseo. " * 12
    assert {("a", "á"), ("o", "ó"), ("fue", "fué"), ("dio", "dió")} <= normas(
        type("P", (), {"normas": normas_de(moderno)}))


@pytest.mark.parametrize("texto", [
    "Fue á casa. " * 9,                       # 9 apariciones: no llega a 10
    "Fue á casa. " * 98 + "Fue a casa. " * 2,  # 98 %: no supera el 99 %
])
def test_sin_bastantes_apariciones_o_proporcion_no_hay_norma(texto):
    assert not [n for n in normas_de(texto) if n.forma_obra in ("á", "a")]


def test_la_raya_pegada_a_la_palabra_no_es_dialogo():
    assert voz.contar_marcas("como aves—así profieren—y los aqueos")["raya de diálogo"] == 0
    marcas = voz.contar_marcas("—¡Atrás, teucros! —gritó Ayax.\n—Bien.")
    assert marcas["raya de diálogo"] == 3


def test_las_comillas_rectas_cuentan_una_vez_por_parlamento():
    assert voz.contar_marcas('Dijo: "Tened ánimo." Y luego: "Bien."')["comillas inglesas"] == 2


# --- Medir ------------------------------------------------------------------

def test_cada_forma_de_norma_rota_es_un_aviso(iliada):
    avisos = voz.normas_rotas("Se volvió a sus compañeros o a las naves. Fue él.", iliada.normas)
    assert sorted(a.cita for a in avisos) == ["Fue", "a", "a", "o"]
    assert all(a.clase == "norma" and a.gravedad == "ruptura" for a in avisos)
    assert "653" in next(a for a in avisos if a.cita == "a").evidencia


def test_las_marcas_de_dialogo_rotas_son_avisos(iliada):
    avisos = voz.normas_rotas('—¡Atrás! —gritó.\nY dijo: "Bien."', iliada.normas)
    assert sorted(a.rasgo for a in avisos) == ["marcas de diálogo"] * 3


def test_candidatos_por_raiz_sin_nombres_propios_ni_formas_de_norma():
    perfil = perfil_de_textos(["Los guerreros pasaron el río y miraron la ciudad."])
    pasaje = "Pasaba Héctor a la ciudad mirando su reloj. Reloj."
    # «Pasaba» y «mirando» comparten raíz con la obra; «Héctor», nombre a
    # mitad de frase; «a», forma de norma; «Reloj» repite «reloj».
    assert candidatos_lexicos(pasaje, perfil.raices) == ["su", "reloj"]


def test_un_pasaje_de_la_obra_se_mide_sin_su_propio_tramo():
    parrafo = "El aguamanos y la daga quedaron en el almo suelo."
    perfil = perfil_de_textos(["Los guerreros pasaron el río.", parrafo])
    assert medir(parrafo, perfil).candidatos == []
    assert {"aguamanos", "daga", "almo"} <= set(medir(parrafo, perfil, excluir=True).candidatos)


# --- Prompt y consulta ------------------------------------------------------

MEDIDA = Medida(avisos_norma=[Aviso("norma", "ortografía", "a", "ruptura", "la obra usa «á»")],
                candidatos=["reloj", "apremiaba"])
PASAJE = "Agamenón pasaba revista mirando su reloj, pues el tiempo apremiaba a todos."


def test_el_prompt_lleva_candidatos_entorno_y_pasaje():
    sistema, usuario = construir_prompt(MEDIDA, Entorno("…lo de antes.", "Lo de después…"), PASAJE)
    assert sistema == voz.PROMPT_SISTEMA
    assert "CANDIDATOS (su raíz no aparece en la obra): reloj, apremiaba" in usuario
    assert "…lo de antes." in usuario and "Lo de después…" in usuario
    assert usuario.index("PASAJE NUEVO") > usuario.index("CANDIDATOS") and PASAJE in usuario


def test_el_prompt_de_sistema_explica_las_reglas():
    for regla in ("SOLO puede ser sobre una palabra de la lista", "LITERAL", '"notas"',
                  '"ruptura"', '"extrañeza"', "listas vacías"):
        assert regla in voz.PROMPT_SISTEMA


def test_consulta_una_llamada_con_el_modelo_del_resto_de_fases(api):
    cliente = api(['```json\n{"avisos": [], "notas": []}\n```'])
    assert consultar_modelo(MEDIDA, Entorno(), PASAJE) == {"avisos": [], "notas": []}
    [peticion] = cliente.peticiones
    assert peticion["model"] == calibrar_glosario.MODELO
    assert peticion["system"] == voz.PROMPT_SISTEMA


# --- Validar ----------------------------------------------------------------

def aviso(**cambios) -> dict:
    return {"palabra": "reloj", "cita": "mirando su reloj", "rasgo": "anacronismo",
            "gravedad": "ruptura", "explicacion": "No hay relojes.", **cambios}


def motivos(informe):
    return [d["motivo"] for d in informe.descartados]


def test_aviso_valido_y_las_normas_van_delante():
    informe = validar({"avisos": [aviso()]}, MEDIDA, PASAJE)
    assert [(a.clase, a.cita) for a in informe.avisos] == [("norma", "a"), ("lexico", "mirando su reloj")]
    lexico = informe.avisos[1]
    assert lexico.palabra == "reloj" and "no aparece en la obra" in lexico.evidencia


@pytest.mark.parametrize("cambio, motivo", [
    ({"cita": "mirando el reloj"}, "cita no literal"),
    ({"cita": ""}, "cita no literal"),
    ({"palabra": "revista", "cita": "pasaba revista"}, "palabra fuera de los candidatos"),
    ({"palabra": "apremiaba"}, "palabra fuera de los candidatos"),   # no está en la cita
    ({"palabra": "a", "cita": "apremiaba a todos"}, "forma de norma"),
    ({"palabra": None}, "forma de norma"),
    ({"rasgo": "caracterizacion"}, "valor no permitido"),
    ({"gravedad": "grave"}, "valor no permitido"),
])
def test_avisos_que_no_se_sostienen_se_descartan(cambio, motivo):
    informe = validar({"avisos": [aviso(**cambio)]}, MEDIDA, PASAJE)
    assert [a.clase for a in informe.avisos] == ["norma"]
    assert motivos(informe) == [motivo]


def test_cita_con_otros_espacios_y_palabra_con_otras_mayusculas_valen():
    informe = validar({"avisos": [aviso(palabra="Reloj", cita="mirando  su\nreloj")]},
                      MEDIDA, PASAJE)
    assert len(informe.avisos) == 2


def test_dos_avisos_sobre_la_misma_palabra_se_quedan_en_el_mas_grave():
    respuesta = {"avisos": [aviso(gravedad="extrañeza"), aviso(), aviso(gravedad="extrañeza")]}
    informe = validar(respuesta, MEDIDA, PASAJE)
    assert [a.gravedad for a in informe.avisos if a.clase == "lexico"] == ["ruptura"]
    assert motivos(informe) == ["repetido", "repetido"]


def test_notas_y_respuesta_vacia():
    informe = validar({"notas": [{"cita": "pasaba", "explicacion": "Imperfecto."}, "basura"]},
                      MEDIDA, PASAJE)
    assert informe.notas == [{"cita": "pasaba", "explicacion": "Imperfecto."}]
    assert validar({}, Medida([], []), PASAJE).avisos == []


# --- Evaluar ----------------------------------------------------------------

TEXTO = "Los guerreros pasaron el río y miraron la ciudad.\n\nLuego descansaron."


def test_sin_candidatos_no_se_llama_al_modelo(api):
    cliente = api([])
    perfil = perfil_de_textos([TEXTO])
    informe = evaluar_en(perfil, TEXTO, "Y los guerreros miraron a la ciudad.", len(TEXTO), cliente)
    assert cliente.peticiones == [] and informe.llamadas == 0
    assert [a.cita for a in informe.avisos] == []   # «a»: la obra es corta, sin normas


def test_con_candidatos_una_llamada(api):
    respuesta = {"avisos": [aviso(cita="su reloj")], "notas": []}
    cliente = api([json.dumps(respuesta)])
    perfil = perfil_de_textos([TEXTO])
    informe = evaluar_en(perfil, TEXTO, "Los guerreros miraron su reloj.", 0)
    assert informe.llamadas == 1 and len(cliente.peticiones) == 1
    assert [a.palabra for a in informe.avisos] == ["reloj"]
    # El entorno es el texto alrededor del punto de inserción.
    assert "Luego descansaron." in cliente.peticiones[0]["messages"][0]["content"]


def test_un_pasaje_que_esta_en_su_sitio_se_mide_sin_su_tramo(api):
    cliente = api([json.dumps({"avisos": [], "notas": []})])
    perfil = perfil_de_textos([TEXTO])
    informe = evaluar_en(perfil, TEXTO, "Luego descansaron.", TEXTO.index("Luego"))
    assert informe.llamadas == 1          # «descansaron» deja de estar en la obra
    assert "descansaron" in cliente.peticiones[0]["messages"][0]["content"]


def test_posicion_fuera_del_fragmento():
    with pytest.raises(ValueError):
        evaluar_en(perfil_de_textos([TEXTO]), TEXTO, "Algo.", len(TEXTO) + 1)
