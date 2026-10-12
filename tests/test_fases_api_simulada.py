"""Fases 0a, 0b y 1 con la API simulada.

Un cliente falso sustituye a anthropic.Anthropic y devuelve las respuestas
reales grabadas en tests/fijos/. Así se prueba, sin coste y siempre igual:
  - qué se le envía al modelo (prompt, fragmentos, caché);
  - cómo se interpreta lo que devuelve (JSON, ```json, errores);
  - que cada script, de principio a fin, escribe lo mismo que escribió
    con esas respuestas el 2026-10-03.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

import calibrar_glosario
import calibrar_taxonomia
import extraer_canon

RAIZ = Path(__file__).resolve().parents[1]
FIJOS = RAIZ / "tests" / "fijos" / "iliada_cantos_i_ii"
CANTOS = [RAIZ / "tests" / "fijos" / "textos" / f"iliada_canto_{n}.txt" for n in ("i", "ii")]


def leer(ruta: Path):
    return json.loads(ruta.read_text(encoding="utf-8"))


def ejecutar_main(monkeypatch, modulo, args):
    monkeypatch.setattr(sys, "argv", [modulo.__file__, *args])
    modulo.main()


def sin_texto_libre(entradas):
    """Lo determinista de una entrada de glosario: la confianza y la
    evidencia elegidas dependen de qué pasada gane (ver reconciliar)."""
    return sorted(
        (e["termino"], e["categoria"], tuple(e["alias"]), tuple(e["variantes"]),
         tuple(e["pasadas_indices"]))
        for e in entradas)


# --- Fase 0b: calibrar_glosario.py ------------------------------------------

def test_glosario_envia_los_fragmentos_marcados_y_cacheados(api):
    cliente = api([(FIJOS / "calibracion_pasada_1.json").read_text(encoding="utf-8")])
    calibrar_glosario.calibrar([str(r) for r in CANTOS])
    [peticion] = cliente.peticiones
    assert peticion["model"] == calibrar_glosario.MODELO
    assert peticion["system"] == calibrar_glosario.PROMPT_SISTEMA
    [bloque] = peticion["messages"][0]["content"]
    assert bloque["cache_control"] == {"type": "ephemeral"}
    assert "=== FRAGMENTO 1 (iliada_canto_i.txt) ===" in bloque["text"]
    assert "=== FRAGMENTO 2 (iliada_canto_ii.txt) ===" in bloque["text"]
    assert bloque["text"].index("FRAGMENTO 1") < bloque["text"].index("FRAGMENTO 2")


@pytest.mark.parametrize("envoltorio", ["{}", "```json\n{}\n```", "```\n{}\n```", "  {}  \n"])
def test_glosario_acepta_json_envuelto_en_markdown(api, envoltorio):
    texto = (FIJOS / "calibracion_pasada_1.json").read_text(encoding="utf-8")
    api([envoltorio.replace("{}", texto)])
    assert calibrar_glosario.calibrar([str(CANTOS[0])]) == json.loads(texto)


def test_pasada_con_json_roto_se_descarta_sin_perder_las_demas(api):
    buena = (FIJOS / "calibracion_pasada_1.json").read_text(encoding="utf-8")
    api([buena, buena[: len(buena) // 2], buena])   # la 2ª, truncada
    resultados = calibrar_glosario.calibrar_n_veces([str(CANTOS[0])], 3)
    assert len(resultados) == 2


def test_si_fallan_todas_las_pasadas_se_aborta(api):
    api(["no es json", "tampoco"])
    with pytest.raises(SystemExit):
        calibrar_glosario.calibrar_n_veces([str(CANTOS[0])], 2)


def test_glosario_de_principio_a_fin_reproduce_lo_grabado(api, monkeypatch, tmp_path):
    api([(FIJOS / f"calibracion_pasada_{i}.json").read_text(encoding="utf-8") for i in (1, 2, 3)])
    monkeypatch.chdir(tmp_path)   # main() escribe en data/<obra>/exports relativo
    ejecutar_main(monkeypatch, calibrar_glosario, ["--obra=iliada", "--n=3", *map(str, CANTOS)])

    salida = tmp_path / "data" / "iliada" / "exports"
    for i in (1, 2, 3):
        assert leer(salida / f"calibracion_pasada_{i}.json") == leer(FIJOS / f"calibracion_pasada_{i}.json")
    for nombre in ("personajes_obra.json", "lugares_obra.json", "glosario_obra.json"):
        esperado = leer(FIJOS / nombre)
        if nombre == "personajes_obra.json":
            esperado = corregir_ayax(esperado)
        assert sin_texto_libre(leer(salida / nombre)) == sin_texto_libre(esperado), nombre
    assert ([d["termino"] for d in leer(salida / "discrepancias_calibracion.json")]
            == [d["termino"] for d in leer(FIJOS / "discrepancias_calibracion.json")] + ["Ayax"])


def corregir_ayax(personajes):
    """Lo grabado el 2026-10-03 tenía el error de los dos Áyax (ver
    test_glosario.py): "Ayax de Oileo" figuraba como alias de Telamonio.
    Hoy el genérico "Ayax" va a discrepancias, ese alias desaparece y
    Telamonio deja de heredar la pasada 1 (que solo dio el genérico); todo
    lo demás debe seguir idéntico."""
    for e in personajes:
        if e["termino"] == "Ayax Telamonio":
            e["alias"] = [a for a in e["alias"] if a != "Ayax de Oileo"]
            e["pasadas_indices"] = [2, 3]
    return personajes


def test_glosario_exige_obra(api, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        ejecutar_main(monkeypatch, calibrar_glosario, [str(CANTOS[0])])
    assert not (tmp_path / "data").exists()


# --- Fase 0a: calibrar_taxonomia.py -----------------------------------------

def test_taxonomia_con_una_pasada_la_copia_como_definitiva(api, monkeypatch, tmp_path):
    cliente = api([(FIJOS / "taxonomia_pasada_1.json").read_text(encoding="utf-8")])
    monkeypatch.chdir(tmp_path)
    ejecutar_main(monkeypatch, calibrar_taxonomia, ["--obra=iliada", *map(str, CANTOS)])

    salida = tmp_path / "data" / "iliada" / "exports"
    assert leer(salida / "taxonomia_pasada_1.json") == leer(FIJOS / "taxonomia_pasada_1.json")
    assert leer(salida / "tipos_documentales_obra.json") == leer(FIJOS / "tipos_documentales_obra.json")
    assert len(cliente.peticiones) == 1


def test_taxonomia_con_varias_pasadas_no_elige_ninguna(api, monkeypatch, tmp_path):
    texto = (FIJOS / "taxonomia_pasada_1.json").read_text(encoding="utf-8")
    api([texto, texto])
    monkeypatch.chdir(tmp_path)
    ejecutar_main(monkeypatch, calibrar_taxonomia, ["--obra=iliada", "--n=2", str(CANTOS[0])])

    salida = tmp_path / "data" / "iliada" / "exports"
    assert (salida / "taxonomia_pasada_2.json").exists()
    assert not (salida / "tipos_documentales_obra.json").exists()


# --- Fase 1: extraer_canon.py -----------------------------------------------

@pytest.fixture
def config_iliada(tmp_path) -> Path:
    """Directorio de config con la calibración grabada, como lo deja la Fase 0."""
    destino = tmp_path / "exports"
    destino.mkdir()
    for nombre in ("tipos_documentales_obra.json", "personajes_obra.json",
                   "lugares_obra.json", "glosario_obra.json"):
        shutil.copy(FIJOS / nombre, destino)
    return destino


def test_prompt_de_extraccion_lleva_la_calibracion_de_la_obra(config_iliada):
    config = extraer_canon.cargar_config_obra(str(config_iliada))
    prompt = extraer_canon.construir_prompt_sistema(config)
    assert prompt.startswith(extraer_canon.PROMPT_SISTEMA_BASE)
    for tipo in config["tipos_documentales"]:
        assert f'"{tipo["nombre"]}"' in prompt
    for e in config["personajes"] + config["lugares"] + config["glosario"]:
        assert f'- {e["termino"]} [{e["categoria"]}]' in prompt


def test_sin_calibracion_el_prompt_es_solo_la_base(tmp_path):
    config = extraer_canon.cargar_config_obra(str(tmp_path))
    assert extraer_canon.construir_prompt_sistema(config) == extraer_canon.PROMPT_SISTEMA_BASE


@pytest.mark.parametrize("orden", [1, 2])
def test_extraccion_de_principio_a_fin(api, monkeypatch, config_iliada, orden):
    respuesta = FIJOS / "respuestas_canon" / f"{orden:04d}.json"
    cliente = api([respuesta.read_text(encoding="utf-8")])
    canto = CANTOS[orden - 1]
    ejecutar_main(monkeypatch, extraer_canon,
                  ["--obra=iliada", f"--orden={orden}", f"--config={config_iliada}", str(canto)])

    [peticion] = cliente.peticiones
    [sistema] = peticion["system"]
    assert sistema["cache_control"] == {"type": "ephemeral"}
    assert "GLOSARIO CALIBRADO" in sistema["text"]
    assert canto.read_text(encoding="utf-8") in peticion["messages"][0]["content"]

    canon = leer(config_iliada / "canon" / f"{orden:04d}.json")
    assert canon.pop("orden_fragmento") == orden
    assert Path(canon.pop("ruta_fragmento_origen")) == canto.resolve()
    assert canon == leer(respuesta)


@pytest.mark.parametrize("orden", [1, 2])
def test_canon_grabado_nombra_con_la_forma_canonica(config_iliada, orden):
    # Misma spec que tests/api comprueba contra la API real.
    config = extraer_canon.cargar_config_obra(str(config_iliada))
    entidades = config["personajes"] + config["lugares"] + config["glosario"]
    terminos = {e["termino"] for e in entidades}
    canonica_de = {f: e["termino"] for e in entidades
                   for f in [*e.get("variantes", []), *e.get("alias", [])]}
    canon = leer(FIJOS / "respuestas_canon" / f"{orden:04d}.json")
    nombrados = (canon["personajes"] + canon["lugares"] + canon["organizaciones"]
                 + [h["atribucion"]["personaje"] for h in canon["hechos_canonicos"]
                    if h["atribucion"]["personaje"]])
    assert not [n for n in nombrados if n in canonica_de and n not in terminos]
    assert canon["tipo_documental"] in {t["nombre"] for t in config["tipos_documentales"]}


def test_extraccion_con_json_roto_guarda_la_respuesta_para_depurar(api, config_iliada):
    api(['{"hechos_canonicos": [{"hecho": "truncado'])
    with pytest.raises(json.JSONDecodeError):
        extraer_canon.extraer_canon("texto", {"tipos_documentales": [], "personajes": [],
                                              "lugares": [], "glosario": []},
                                    directorio_debug=str(config_iliada))
    depuracion = config_iliada / "_ultimo_error_respuesta_cruda.txt"
    assert depuracion.read_text(encoding="utf-8").startswith('{"hechos_canonicos"')


@pytest.mark.parametrize("args", [
    ["--orden=1", "x.txt"],          # sin obra
    ["--obra=iliada", "x.txt"],      # sin orden
    ["--obra=iliada", "--orden=1"],  # sin fragmento
])
def test_extraccion_exige_obra_orden_y_fragmento(api, monkeypatch, args):
    cliente = api([])
    with pytest.raises(SystemExit):
        ejecutar_main(monkeypatch, extraer_canon, args)
    assert cliente.peticiones == []   # no se gasta ninguna llamada
