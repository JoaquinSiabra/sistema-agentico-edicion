"""Fase 0b (calibrar_glosario.py): reconciliación de pasadas y fusión de alias.

Sin API: se prueba lo que se hace con las respuestas del modelo, no la
llamada en sí.
"""

from calibrar_glosario import (
    construir_registros, fusionar_alias, reconciliar, repartir, separar_ambiguos,
)


def entrada(termino, categoria="persona", variantes=(), confianza="alta"):
    return {"termino": termino, "categoria": categoria,
            "variantes": list(variantes), "confianza": confianza}


def pasada(*entradas):
    return {"glosario": list(entradas)}


# --- reconciliar ---------------------------------------------------------

def test_mayoria_fija_la_categoria_y_registra_el_disenso():
    estables, discrepancias = reconciliar([
        pasada(entrada("Olimpo", "lugar")),
        pasada(entrada("Olimpo", "lugar")),
        pasada(entrada("Olimpo", "concepto_abstracto")),
    ])
    assert discrepancias == []
    olimpo = estables["Olimpo"]
    assert olimpo["categoria"] == "lugar"
    assert olimpo["disenso"] == [{"categoria": "concepto_abstracto", "confianza": "alta"}]
    assert olimpo["pasadas_que_lo_mencionan"] == 3
    assert olimpo["pasadas_indices"] == [1, 2, 3]


def test_entre_pasadas_que_coinciden_gana_la_de_mayor_confianza():
    estables, _ = reconciliar([
        pasada({**entrada("Crises", confianza="baja"), "evidencia": "dudosa"}),
        pasada({**entrada("Crises", confianza="alta"), "evidencia": "habla en vocativo"}),
        pasada({**entrada("Crises", confianza="media"), "evidencia": "suplica"}),
    ])
    assert estables["Crises"]["confianza"] == "alta"
    assert estables["Crises"]["evidencia"] == "habla en vocativo"


def test_empate_va_a_discrepancias_y_no_a_estables():
    estables, discrepancias = reconciliar([
        pasada(entrada("Ate", "persona")),
        pasada(entrada("Ate", "concepto_abstracto")),
        pasada(),
    ])
    assert "Ate" not in estables
    assert [d["termino"] for d in discrepancias] == ["Ate"]
    assert discrepancias[0]["pasadas_que_lo_mencionan"] == 2
    assert discrepancias[0]["pasadas_totales"] == 3


def test_termino_mencionado_en_una_sola_pasada_se_conserva():
    estables, _ = reconciliar([pasada(entrada("Tersites")), pasada(), pasada()])
    assert estables["Tersites"]["pasadas_indices"] == [1]
    assert estables["Tersites"]["pasadas_totales"] == 3


def test_variantes_se_unen_entre_pasadas_sin_repetir_el_termino():
    estables, _ = reconciliar([
        pasada(entrada("Aquiles", variantes=["Pelida", "Aquiles"])),
        pasada(entrada("Aquiles", variantes=["Eácida", "Pelida"])),
    ])
    assert estables["Aquiles"]["variantes"] == ["Eácida", "Pelida"]


def test_terminos_vacios_se_ignoran():
    estables, discrepancias = reconciliar([pasada(entrada("  "), entrada(""))])
    assert estables == {} and discrepancias == []


# --- repartir ------------------------------------------------------------

def test_repartir_separa_personajes_lugares_y_resto():
    personajes, lugares, glosario = repartir(pasada(
        entrada("Aquiles", "persona"),
        entrada("Troya", "lugar"),
        entrada("aqueos", "pueblo"),
        entrada("néctar", "objeto"),
        entrada("Ate", "incierto"),
    ))
    assert [e["termino"] for e in personajes] == ["Aquiles"]
    assert [e["termino"] for e in lugares] == ["Troya"]
    assert [e["termino"] for e in glosario] == ["aqueos", "néctar", "Ate"]


# --- fusionar_alias ------------------------------------------------------

def test_fusiona_por_variante_reportada():
    resultado = fusionar_alias([
        entrada("Agamenón", variantes=["Atrida Agamenón"]),
        entrada("Atrida Agamenón"),
    ])
    # Gana el término más largo; el corto pasa a alias.
    assert [(e["termino"], e["alias"]) for e in resultado] == [("Atrida Agamenón", ["Agamenón"])]


def test_substring_solo_si_se_permite():
    entradas = [entrada("Héctor"), entrada("Héctor Priámida")]
    assert len(fusionar_alias(entradas, permitir_substring=False)) == 2
    fusionado = fusionar_alias(entradas, permitir_substring=True)
    assert [e["termino"] for e in fusionado] == ["Héctor Priámida"]
    assert fusionado[0]["alias"] == ["Héctor"]


def test_substring_exige_palabra_completa():
    # "Ilo" está dentro de "Ilión" pero no como palabra: no se fusionan.
    resultado = fusionar_alias([entrada("Ilo"), entrada("Ilión")], permitir_substring=True)
    assert len(resultado) == 2


def test_no_modifica_las_entradas_originales():
    original = entrada("Héctor", variantes=["Priámida"])
    fusionar_alias([original, entrada("Priámida")])
    assert "alias" not in original


def test_fusion_une_las_pasadas():
    a = {**entrada("Héctor Priámida"), "pasadas_indices": [1, 2]}
    b = {**entrada("Héctor"), "pasadas_indices": [2, 3]}
    [fusionado] = fusionar_alias([a, b], permitir_substring=True)
    assert fusionado["pasadas_indices"] == [1, 2, 3]
    assert fusionado["pasadas_que_lo_mencionan"] == 3


# Caso real de la calibración de los Cantos I-III de la Ilíada (3 pasadas).
PASADAS_AYAX = [
    pasada(entrada("Ayax", variantes=["Ayax Telamonio", "Ayax de Oileo"])),
    pasada(entrada("Ayax Telamonio", variantes=["Ayax", "Ayax de Telamón"]),
           entrada("Ayax de Oileo", variantes=["ligero Ayax", "menor Ayax"])),
    pasada(entrada("Ayax Telamonio", variantes=["Ayax", "Ayax de Salamina"]),
           entrada("Ayax de Oileo", variantes=["ligero Ayax", "Ayax menor"])),
]


def test_regresion_dos_ayax_no_se_confunden():
    # El "Ayax" genérico de la pasada 1 daba como variantes a los dos Áyax;
    # al fusionarlo con Telamonio, "Ayax de Oileo" acababa como alias suyo.
    estables, _ = reconciliar(PASADAS_AYAX)
    registros, ambiguas = construir_registros(estables)
    personajes = registros["personajes"]

    terminos = {e["termino"] for e in personajes}
    assert terminos == {"Ayax Telamonio", "Ayax de Oileo"}
    for e in personajes:
        otros = terminos - {e["termino"]}
        assert not set(e["alias"]) & otros, (
            f"{e['termino']} tiene como alias a otra entidad: {set(e['alias']) & otros}")
    # El genérico no se pierde en silencio: queda para revisar a mano.
    assert [(a["termino"], a["entidades"]) for a in ambiguas] == [
        ("Ayax", ["Ayax Telamonio", "Ayax de Oileo"])]
    # Las pasadas 2 y 3 sí dieron "Ayax" como variante de Telamonio.
    telamonio = next(e for e in personajes if e["termino"] == "Ayax Telamonio")
    assert "Ayax" in telamonio["alias"]


def test_fusion_nunca_deja_como_alias_a_otra_entidad():
    # Aunque una variante reportada apunte a otra entidad que sobrevive.
    resultado = fusionar_alias([
        entrada("Héctor", variantes=["Priámida"]),
        entrada("Priámida", variantes=["Héctor", "Paris"]),
        entrada("Paris"),
    ])
    terminos = {e["termino"] for e in resultado}
    for e in resultado:
        assert not set(e["alias"]) & (terminos - {e["termino"]})


def test_separar_ambiguos_solo_aparta_paraguas():
    limpias, ambiguas = separar_ambiguos([
        entrada("Atridas", variantes=["Agamenón", "Menelao"]),
        entrada("Agamenón", variantes=["Atrida"]),
        entrada("Menelao"),
    ])
    assert [e["termino"] for e in limpias] == ["Agamenón", "Menelao"]
    assert ambiguas[0]["termino"] == "Atridas"
    assert ambiguas[0]["entidades"] == ["Agamenón", "Menelao"]
