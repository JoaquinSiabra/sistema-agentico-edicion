"""Calibración incremental del glosario (calibrar_glosario.py).

Parte del glosario fijado de los Cantos I-II (tests/fijos/) y simula lo
que traería un canto nuevo. La garantía principal: nada de lo ya fijado
desaparece ni cambia, así que el canon ya extraído sigue siendo válido.
"""

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

import calibrar_glosario
from calibrar_glosario import REGISTROS, incorporar

RAIZ = Path(__file__).resolve().parents[1]
FIJOS = RAIZ / "tests" / "fijos" / "iliada_cantos_i_ii"
CANTO_III = RAIZ / "tests" / "fijos" / "textos" / "iliada_canto_iii.txt"


def leer(ruta: Path):
    return json.loads(ruta.read_text(encoding="utf-8"))


@pytest.fixture
def fijado() -> dict[str, list[dict]]:
    return {k: leer(FIJOS / f) for k, f in REGISTROS.items()}


def estable(termino, categoria="persona", variantes=(), evidencia="..."):
    """Una entrada como las que deja reconciliar()."""
    return {"termino": termino, "categoria": categoria, "variantes": list(variantes),
            "evidencia": evidencia, "confianza": "alta",
            "pasadas_que_lo_mencionan": 3, "pasadas_totales": 3,
            "pasadas_indices": [1, 2, 3], "disenso": []}


def buscar(registros, termino):
    return next((nombre, e) for nombre, lista in registros.items()
                for e in lista if e["termino"] == termino)


def terminos(registros, nombre=None):
    return {e["termino"] for k, lista in registros.items() if nombre in (None, k) for e in lista}


# --- Lo fijado no se toca -----------------------------------------------------

def test_sin_novedades_no_cambia_nada(fijado):
    original = copy.deepcopy(fijado)
    registros, discrepancias = incorporar(fijado, {})
    assert registros == original and discrepancias == []
    assert fijado == original   # tampoco modifica la entrada


def test_ningun_termino_fijado_desaparece_ni_cambia_de_categoria(fijado):
    registros, _ = incorporar(fijado, {
        "Alejandro": estable("Alejandro", variantes=["Paris"]),
        "Helena": estable("Helena", "concepto_abstracto"),          # choca
        "Pelida": estable("Pelida"),                                  # variante conocida
        "Ayax": estable("Ayax", variantes=["Ayax Telamonio", "Ayax de Oileo"]),  # ambiguo
    })
    for nombre, lista in fijado.items():
        for e in lista:
            nombre_ahora, ahora = buscar(registros, e["termino"])
            assert nombre_ahora == nombre and ahora["categoria"] == e["categoria"]
            assert set(e["alias"]) <= set(ahora["alias"])


def test_canon_ya_extraido_sigue_valido(fijado):
    # Todo nombre que usa el canon grabado de los Cantos I-II sigue siendo un
    # término canónico tras incorporar un canto nuevo: no hay que reextraer.
    registros, _ = incorporar(fijado, {"Alejandro": estable("Alejandro")})
    validos = terminos(registros)
    for n in ("0001", "0002"):
        canon = leer(FIJOS / "respuestas_canon" / f"{n}.json")
        usados = {p for p in canon["personajes"] + canon["lugares"] if p in terminos(fijado)}
        usados |= {h["atribucion"]["personaje"] for h in canon["hechos_canonicos"]
                   if h["atribucion"]["personaje"]}
        assert usados <= validos


# --- Lo nuevo se añade donde toca ----------------------------------------------

def test_termino_nuevo_va_a_su_registro(fijado):
    registros, discrepancias = incorporar(fijado, {
        "Alejandro": estable("Alejandro", variantes=["Paris"]),
        "puertas Esceas": estable("puertas Esceas", "lugar"),
        "cinto bordado": estable("cinto bordado", "objeto"),
    })
    assert discrepancias == []
    assert buscar(registros, "Alejandro")[0] == "personajes"
    assert buscar(registros, "Alejandro")[1]["alias"] == ["Paris"]
    assert buscar(registros, "puertas Esceas")[0] == "lugares"
    assert buscar(registros, "cinto bordado")[0] == "glosario"
    assert len(terminos(registros)) == len(terminos(fijado)) + 3


def test_forma_nueva_de_un_termino_fijado_se_le_anade(fijado):
    registros, _ = incorporar(fijado, {"Aquiles": estable("Aquiles", variantes=["Pelida divino"])})
    aquiles = buscar(registros, "Aquiles")[1]
    assert "Pelida divino" in aquiles["alias"] and "Pelida divino" in aquiles["variantes"]
    assert len(terminos(registros)) == len(terminos(fijado))


def test_variante_conocida_no_se_duplica_como_termino(fijado):
    # El modelo devuelve "Pelida" como si fuera nuevo: es una variante de Aquiles.
    registros, discrepancias = incorporar(fijado, {"Pelida": estable("Pelida", variantes=["Aquiles"])})
    assert "Pelida" not in terminos(registros)
    assert registros == fijado and discrepancias == []


def test_nueva_por_variante_que_es_un_termino_fijado(fijado):
    # Nombre nuevo cuya variante reportada es un término fijado: es esa entidad.
    registros, _ = incorporar(fijado, {"rey de hombres": estable("rey de hombres", variantes=["Agamenón"])})
    assert "rey de hombres" not in terminos(registros)
    assert "rey de hombres" in buscar(registros, "Agamenón")[1]["alias"]


def test_compartir_un_epiteto_no_identifica_a_la_entidad(fijado):
    # Caso real del Canto III: Climene ("la de los grandes ojos", epíteto
    # que el glosario ya tenía como alias de Juno) acababa absorbida por Juno.
    juno = buscar(fijado, "Juno")[1]
    assert "la de los grandes ojos" in juno["alias"]
    registros, discrepancias = incorporar(fijado, {
        "Climene": estable("Climene", variantes=["Climene la de los grandes ojos",
                                                 "la de los grandes ojos"])})
    assert discrepancias == []
    assert buscar(registros, "Juno")[1] == juno
    climene = buscar(registros, "Climene")[1]
    assert climene["alias"] == ["Climene la de los grandes ojos"]   # sin el epíteto de Juno


def test_forma_de_otra_entidad_no_se_reasigna(fijado):
    # "Pelida" es de Aquiles: no puede pasar a ser alias de Agamenón.
    registros, _ = incorporar(fijado, {"Agamenón": estable("Agamenón", variantes=["Pelida", "Atrida"])})
    assert "Pelida" not in buscar(registros, "Agamenón")[1]["alias"]
    assert "Pelida" in buscar(registros, "Aquiles")[1]["alias"]


# --- Lo que choca va a discrepancias -------------------------------------------

def test_categoria_distinta_va_a_discrepancias(fijado):
    registros, discrepancias = incorporar(fijado, {"Helena": estable("Helena", "concepto_abstracto")})
    assert buscar(registros, "Helena")[1]["categoria"] == "persona"
    [d] = discrepancias
    assert (d["termino"], d["categoria_fijada"], d["categoria_propuesta"]) == (
        "Helena", "persona", "concepto_abstracto")


def test_nombre_de_varias_entidades_va_a_discrepancias(fijado):
    registros, discrepancias = incorporar(fijado, {
        "Ayax": estable("Ayax", variantes=["Ayax Telamonio", "Ayax de Oileo"])})
    assert registros == fijado
    [d] = discrepancias
    assert d["termino"] == "Ayax" and d["entidades"] == ["Ayax Telamonio", "Ayax de Oileo"]


# --- El script, de principio a fin, con la API simulada --------------------------

@pytest.fixture
def obra_calibrada(tmp_path, monkeypatch) -> Path:
    """data/iliada/exports con el glosario fijado de los Cantos I-II."""
    salida = tmp_path / "data" / "iliada" / "exports"
    salida.mkdir(parents=True)
    for f in [*REGISTROS.values(), "discrepancias_calibracion.json"]:
        shutil.copy(FIJOS / f, salida)
    monkeypatch.chdir(tmp_path)
    return salida


RESPUESTA_CANTO_III = json.dumps({"glosario": [
    {"termino": "Alejandro", "categoria": "persona", "variantes": ["Paris"],
     "evidencia": "desafía a los aqueos", "confianza": "alta"},
    {"termino": "Helena", "categoria": "persona", "variantes": ["Helena argiva"],
     "evidencia": "habla con Príamo en la muralla", "confianza": "alta"},
]}, ensure_ascii=False)


def test_incremental_de_principio_a_fin(api, monkeypatch, obra_calibrada):
    cliente = api([RESPUESTA_CANTO_III] * 3)
    monkeypatch.setattr(sys, "argv", ["calibrar_glosario.py", "--obra=iliada", str(CANTO_III)])
    calibrar_glosario.main()

    # Se envía solo el canto nuevo, con el glosario fijado en el prompt.
    sistema = cliente.peticiones[0]["system"]
    assert calibrar_glosario.PROMPT_INCREMENTAL in sistema
    assert "- Aquiles [persona]" in sistema and "- Troya [lugar]" in sistema
    texto = cliente.peticiones[0]["messages"][0]["content"][0]["text"]
    assert "iliada_canto_iii.txt" in texto and "iliada_canto_i.txt" not in texto

    # Las pasadas nuevas no pisan las de la calibración original.
    assert sorted(p.name for p in obra_calibrada.glob("calibracion_*")) == [
        f"calibracion_iliada_canto_iii_pasada_{i}.json" for i in (1, 2, 3)]

    personajes = leer(obra_calibrada / "personajes_obra.json")
    fijados = leer(FIJOS / "personajes_obra.json")
    assert [e["termino"] for e in personajes[:len(fijados)]] == [e["termino"] for e in fijados]
    assert personajes[-1]["termino"] == "Alejandro"
    assert "Helena argiva" in next(e for e in personajes if e["termino"] == "Helena")["alias"]
    assert leer(obra_calibrada / "lugares_obra.json") == leer(FIJOS / "lugares_obra.json")

    # Las discrepancias anteriores se conservan.
    discrepancias = leer(obra_calibrada / "discrepancias_calibracion.json")
    assert discrepancias == leer(FIJOS / "discrepancias_calibracion.json")


def test_desde_cero_ignora_el_glosario_fijado(api, monkeypatch, obra_calibrada):
    cliente = api([RESPUESTA_CANTO_III])
    monkeypatch.setattr(sys, "argv", ["calibrar_glosario.py", "--obra=iliada", "--n=1",
                                      "--desde-cero", str(CANTO_III)])
    calibrar_glosario.main()

    assert cliente.peticiones[0]["system"] == calibrar_glosario.PROMPT_SISTEMA
    assert (obra_calibrada / "calibracion_pasada_1.json").exists()
    assert [e["termino"] for e in leer(obra_calibrada / "personajes_obra.json")] == [
        "Alejandro", "Helena"]
