"""Base de datos de prueba para el nivel 2 (plugin de tests/conftest.py,
para que lo compartan tests/db/ y tests/continuidad/).

Crea desde cero `almacen_test` (nunca toca `almacen`), aplica schema.sql
y carga los Cantos I y II de la Ilíada con poblar_almacen.poblar, una sola
vez por sesión, a partir de los datos fijos de tests/fijos/ (no de las
carpetas de trabajo, que cambian al añadir cantos). Se deja creada al terminar para poder inspeccionarla.

Los embeddings se guardan en .cache/ (fuera de git): calcularlos en CPU
cuesta minutos y son deterministas para un mismo modelo y texto. Borrar
esa carpeta fuerza a recalcularlos.

Variables de entorno (opcionales):
    NOVELA_DSN_ADMIN  conexión con permiso para crear bases de datos
                      (por defecto, el usuario editor contra `postgres`)
"""

import hashlib
import json
import os
import pickle
import shutil
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import psycopg2
import pytest

from poblar_almacen import MODELO_EMBEDDING, cargar_modelo, poblar

RAIZ = Path(__file__).resolve().parents[1]
FIJOS = RAIZ / "tests" / "fijos" / "iliada_cantos_i_ii"
FIJOS_III_IV = RAIZ / "tests" / "fijos" / "iliada_cantos_iii_iv"
CANTOS = {1: "i", 2: "ii", 3: "iii", 4: "iv"}
TEXTOS = RAIZ / "tests" / "fijos" / "textos"
NOMBRE_BD = "almacen_test"
DSN_ADMIN = os.environ.get(
    "NOVELA_DSN_ADMIN",
    "dbname=postgres user=editor password=editor_dev host=localhost port=5432",
)
OBRA = "iliada"
CACHE_EMBEDDINGS = RAIZ / ".cache" / f"embeddings_{MODELO_EMBEDDING.replace('/', '_')}.pkl"


def dsn_test() -> str:
    partes = dict(p.split("=", 1) for p in DSN_ADMIN.split())
    partes["dbname"] = NOMBRE_BD
    return " ".join(f"{k}={v}" for k, v in partes.items())


def exports_de_prueba(destino: Path, glosario: Path = FIJOS,
                      canon_de: dict[int, Path] | None = None) -> Path:
    """Monta data/<obra>/exports como lo deja la Fase 1: el glosario fijado
    y un canon/NNNN.json por canto, con las dos claves que añade
    extraer_canon.py apuntando a los textos de tests/fijos/textos.
    Por defecto, los Cantos I-II; `canon_de` dice de qué carpeta sale el
    canon de cada canto."""
    (destino / "canon").mkdir(parents=True)
    for nombre in ("personajes_obra.json", "lugares_obra.json", "glosario_obra.json"):
        shutil.copy(glosario / nombre, destino)
    for orden, fijos in (canon_de or {1: FIJOS, 2: FIJOS}).items():
        canto = CANTOS[orden]
        canon = json.loads((fijos / "respuestas_canon" / f"{orden:04d}.json").read_text(encoding="utf-8"))
        canon["orden_fragmento"] = orden
        canon["ruta_fragmento_origen"] = str(TEXTOS / f"iliada_canto_{canto}.txt")
        (destino / "canon" / f"{orden:04d}.json").write_text(
            json.dumps(canon, ensure_ascii=False), encoding="utf-8")
    return destino


@pytest.fixture(scope="session")
def obra() -> str:
    return OBRA


class ModeloConCache:
    """Envuelve el SentenceTransformer: mismo encode() y tokenizer, pero
    reutiliza los vectores ya calculados (clave: hash del texto, que ya
    incluye el prefijo e5 "passage: "/"query: ")."""

    def __init__(self, modelo, ruta: Path):
        self.modelo, self.tokenizer, self.ruta = modelo, modelo.tokenizer, ruta
        self.vectores = pickle.loads(ruta.read_bytes()) if ruta.exists() else {}

    def encode(self, textos, **opciones):
        uno_solo = isinstance(textos, str)
        lista = [textos] if uno_solo else list(textos)
        claves = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in lista]
        nuevos = [(c, t) for c, t in dict(zip(claves, lista)).items() if c not in self.vectores]
        if nuevos:
            vectores = self.modelo.encode([t for _, t in nuevos], **opciones)
            self.vectores.update(zip((c for c, _ in nuevos), vectores))
            self.ruta.parent.mkdir(exist_ok=True)
            self.ruta.write_bytes(pickle.dumps(self.vectores))
        resultado = np.array([self.vectores[c] for c in claves])
        return resultado[0] if uno_solo else resultado


@pytest.fixture(scope="session")
def modelo():
    return ModeloConCache(cargar_modelo(), CACHE_EMBEDDINGS)


@pytest.fixture(scope="session")
def exports(tmp_path_factory) -> Path:
    return exports_de_prueba(tmp_path_factory.mktemp("iliada") / "exports")


@pytest.fixture(scope="session")
def bd(modelo, exports) -> str:
    """DSN de una base de datos recién creada y poblada con la Ilíada."""
    try:
        admin = psycopg2.connect(DSN_ADMIN)
    except psycopg2.OperationalError as e:
        pytest.exit(f"No hay conexión con Postgres ({e}). ¿Se está ejecutando desde WSL?")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {NOMBRE_BD}")
        cur.execute(f"CREATE DATABASE {NOMBRE_BD}")
    admin.close()

    con = psycopg2.connect(dsn_test())
    with con.cursor() as cur:
        cur.execute((RAIZ / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    poblar(con, modelo, OBRA, exports)
    con.close()
    return dsn_test()


@pytest.fixture
def cur(bd):
    con = psycopg2.connect(bd)
    try:
        with con.cursor() as cursor:
            yield cursor
    finally:
        con.rollback()
        con.close()


@contextmanager
def obra_aparte(bd, modelo, slug: str, exports: Path):
    """Carga otra obra en la base de prueba y la borra al terminar: las
    pruebas de carga cuentan todo lo que hay en la base."""
    con = psycopg2.connect(bd)
    try:
        poblar(con, modelo, slug, exports)
        yield con
    finally:
        con.rollback()
        with con.cursor() as cur:
            cur.execute("DELETE FROM obras WHERE slug = %s", (slug,))
        con.commit()
        con.close()

