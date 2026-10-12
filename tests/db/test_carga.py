"""Ingesta (poblar_almacen.py) sobre la base de datos de prueba."""

import json
import shutil

import psycopg2
import pytest

from poblar_almacen import poblar

pytestmark = pytest.mark.db


def leer_json(ruta):
    return json.loads(ruta.read_text(encoding="utf-8"))


def huella(cur) -> dict:
    """Todo lo que la carga escribe, en forma comparable."""
    consultas = {
        "obras": "SELECT slug FROM obras ORDER BY slug",
        "entidades": "SELECT termino, categoria FROM entidades ORDER BY termino",
        "fragmentos": "SELECT orden_fragmento, md5(texto) FROM fragmentos ORDER BY 1",
        "pasajes": """SELECT posicion_global, inicio_char, fin_char, md5(texto)
                      FROM pasajes ORDER BY posicion_global""",
        "hechos": """SELECT posicion_global, hecho, atribucion_tipo
                     FROM hechos_canonicos ORDER BY posicion_global""",
        "menciones": """SELECT f.orden_fragmento, e.termino FROM fragmento_entidad fe
                        JOIN fragmentos f ON f.id = fe.fragmento_id
                        JOIN entidades e ON e.id = fe.entidad_id ORDER BY 1, 2""",
    }
    resultado = {}
    for nombre, sql in consultas.items():
        cur.execute(sql)
        resultado[nombre] = cur.fetchall()
    return resultado


def test_un_fragmento_por_fichero_de_canon(cur, exports):
    canon = sorted((exports / "canon").glob("*.json"))
    cur.execute("SELECT orden_fragmento FROM fragmentos ORDER BY 1")
    assert [f for (f,) in cur.fetchall()] == [leer_json(r)["orden_fragmento"] for r in canon]


def test_todos_los_hechos_se_cargan_en_orden(cur, exports):
    for ruta in sorted((exports / "canon").glob("*.json")):
        canon = leer_json(ruta)
        cur.execute(
            """SELECT h.hecho, h.posicion_global FROM hechos_canonicos h
               JOIN fragmentos f ON f.id = h.fragmento_id
               WHERE f.orden_fragmento = %s ORDER BY h.posicion_global""",
            (canon["orden_fragmento"],),
        )
        filas = cur.fetchall()
        assert [h for h, _ in filas] == [h["hecho"] for h in canon["hechos_canonicos"]]
        assert [p for _, p in filas] == [
            canon["orden_fragmento"] * 10000 + i for i in range(len(filas))]


def test_glosario_completo_como_entidades(cur, exports):
    terminos = {
        e["termino"]
        for nombre in ("personajes_obra.json", "lugares_obra.json", "glosario_obra.json")
        for e in leer_json(exports / nombre)
    }
    cur.execute("SELECT termino FROM entidades")
    assert {t for (t,) in cur.fetchall()} == terminos


def test_quien_afirma_queda_enlazado_a_su_entidad(cur):
    cur.execute(
        """SELECT count(*) FILTER (WHERE atribucion_tipo <> 'narrador_documental'
                                   AND atribucion_personaje_id IS NULL),
                  count(*) FILTER (WHERE atribucion_tipo = 'narrador_documental'
                                   AND atribucion_personaje_id IS NOT NULL)
           FROM hechos_canonicos""")
    sin_enlace, enlace_de_mas = cur.fetchone()
    assert sin_enlace == 0
    assert enlace_de_mas == 0


def test_pasajes_reproducen_el_texto_del_fragmento(cur):
    cur.execute(
        """SELECT f.texto, array_agg(p.inicio_char ORDER BY p.orden_en_fragmento),
                  array_agg(p.fin_char ORDER BY p.orden_en_fragmento),
                  array_agg(p.texto ORDER BY p.orden_en_fragmento)
           FROM fragmentos f JOIN pasajes p ON p.fragmento_id = f.id
           GROUP BY f.id""")
    for texto, inicios, fines, textos in cur.fetchall():
        for a, b, t in zip(inicios, fines, textos):
            assert texto[a:b] == t   # cita exacta por desplazamiento
        assert texto[:inicios[0]].strip() == "" and texto[fines[-1]:].strip() == ""
        for b, a in zip(fines, inicios[1:]):
            assert texto[b:a].strip() == ""   # sin huecos (si se solapan, b > a y es vacío)


def test_ningun_pasaje_supera_el_limite_de_e5(cur, modelo):
    # El límite real del modelo son 512 tokens con especiales y el prefijo.
    cur.execute("SELECT texto FROM pasajes")
    for (texto,) in cur.fetchall():
        tokens = modelo.tokenizer(f"passage: {texto}")["input_ids"]
        assert len(tokens) <= 512


def test_recargar_no_cambia_nada(bd, modelo, exports, obra):
    con = psycopg2.connect(bd)
    try:
        with con.cursor() as cur:
            antes = huella(cur)
        con.rollback()
        poblar(con, modelo, obra, exports)
        with con.cursor() as cur:
            assert huella(cur) == antes
    finally:
        con.close()


def test_carga_fallida_no_deja_nada_a_medias(bd, modelo, exports, tmp_path):
    roto = tmp_path / "exports"
    (roto / "canon").mkdir(parents=True)
    canon = leer_json(exports / "canon" / "0001.json")
    canon["ruta_fragmento_origen"] = str(tmp_path / "no_existe.txt")
    (roto / "canon" / "0001.json").write_text(json.dumps(canon), encoding="utf-8")

    con = psycopg2.connect(bd)
    try:
        with con.cursor() as cur:
            antes = huella(cur)
        con.rollback()
        with pytest.raises(FileNotFoundError):
            poblar(con, modelo, "obra_rota", roto)
        with con.cursor() as cur:
            assert huella(cur) == antes
    finally:
        con.close()


def test_glosario_ampliado_actualiza_las_entidades(bd, modelo, exports, tmp_path):
    # Como tras una calibración incremental: un alias nuevo en un término
    # fijado y un término nuevo. Se carga como otra obra y se borra al final
    # para no alterar la base de datos que usan las demás pruebas.
    ampliado = tmp_path / "exports"
    shutil.copytree(exports, ampliado)
    personajes = leer_json(ampliado / "personajes_obra.json")
    aquiles = next(e for e in personajes if e["termino"] == "Aquiles")
    aquiles["alias"] = aquiles["alias"] + ["Pelida divino"]
    personajes.append({"termino": "Alejandro", "categoria": "persona",
                       "alias": ["Paris"], "variantes": ["Paris"], "confianza": "alta"})
    (ampliado / "personajes_obra.json").write_text(json.dumps(personajes), encoding="utf-8")

    con = psycopg2.connect(bd)
    try:
        poblar(con, modelo, "iliada_ampliada", exports)
        poblar(con, modelo, "iliada_ampliada", ampliado)
        with con.cursor() as cur:
            cur.execute("""SELECT e.termino, e.alias FROM entidades e JOIN obras o ON o.id = e.obra_id
                           WHERE o.slug = 'iliada_ampliada' AND e.termino IN ('Aquiles', 'Alejandro')""")
            alias = dict(cur.fetchall())
        assert "Pelida divino" in alias["Aquiles"]
        assert alias["Alejandro"] == ["Paris"]
    finally:
        with con.cursor() as cur:
            cur.execute("DELETE FROM obras WHERE slug = 'iliada_ampliada'")
        con.commit()
        con.close()
