"""
poblar_almacen.py

Fase 2 del bootstrapping: cargar en el Almacén (Postgres + pgvector) la
doble representación de la obra.

  (a) PROSA VECTORIZADA: el texto de cada fragmento + su embedding, para
      recuperación semántica ("qué pasajes hablan de X").
  (b) FICHAS CANÓNICAS: los hechos_canonicos ya extraídos en la Fase 1
      (extraer_canon.py), con su momento_canonico y atribución, para
      filtrado estructurado — la base del filtro anti-spoiler que usará
      el retriever híbrido (fase siguiente, no implementada aquí).

Ambas representaciones comparten la misma clave de orden de lectura
(orden_fragmento, orden_en_fragmento -> posicion_global) para que el
filtrado anti-spoiler sea un WHERE posicion_global <= N, sin re-derivar
el orden narrativo en tiempo de consulta.

PRERREQUISITO — cambio necesario en extraer_canon.py:
Este script asume que la Fase 1 ya NO sobreescribe siempre un único
canon_fragmento.json, sino que escribe un fichero POR FRAGMENTO bajo
    data/<obra>/exports/canon/{orden:04d}.json
y que cada uno incluye dos claves que el esquema actual no trae:
    "orden_fragmento":      <int>   posición de lectura de este fragmento
    "ruta_fragmento_origen": "<ruta al .txt que se le pasó al modelo>"
Sin esas dos claves no hay ni orden ni texto que vectorizar. Es un cambio
pequeño y localizado (los detalles van en el mensaje, no en este fichero,
para no mezclar Fase 1 y Fase 2 en el mismo script).

EMBEDDINGS — modelo local (sentence-transformers,
intfloat/multilingual-e5-large, 1024 dim) en vez de una API de pago: la
obra es un corpus pequeño (una novela), así que el coste en CPU del N100
es asumible como proceso por lotes, no interactivo, y no añade
dependencia de red ni coste variable. Si el volumen crece o la latencia
de indexado molesta, la alternativa es Voyage AI (voyage-3) a cambio de
red + coste por token — cambiar de modelo implica migrar la columna
`embedding` (dimensión fija en schema.sql) y reindexar.

Uso:
    uv run python poblar_almacen.py --obra=nombre_obra \
        --exports=data/nombre_obra/exports
"""

import argparse
import json
from pathlib import Path

import psycopg2
from sentence_transformers import SentenceTransformer

MODELO_EMBEDDING = "intfloat/multilingual-e5-large"
DSN_DEFAULT = "dbname=almacen user=editor password=editor_dev host=localhost port=5432"


def cargar_modelo() -> SentenceTransformer:
    return SentenceTransformer(MODELO_EMBEDDING)


def embed_pasaje(modelo: SentenceTransformer, texto: str) -> list[float]:
    # Convención e5: prefijo "passage: " para texto indexado. El futuro
    # retriever deberá usar "query: " para el texto de la consulta —
    # mezclar los dos prefijos es el error más común con esta familia
    # de modelos y degrada silenciosamente la similitud.
    return modelo.encode(f"passage: {texto}", normalize_embeddings=True).tolist()


def obtener_o_crear_obra(cur, slug: str) -> int:
    cur.execute("SELECT id FROM obras WHERE slug = %s", (slug,))
    fila = cur.fetchone()
    if fila:
        return fila[0]
    cur.execute(
        "INSERT INTO obras (slug, titulo) VALUES (%s, %s) RETURNING id",
        (slug, slug),
    )
    return cur.fetchone()[0]


def obtener_o_crear_entidad(
    cur, obra_id: int, termino: str, categoria: str,
    alias=None, variantes=None, confianza=None,
) -> int:
    cur.execute(
        "SELECT id FROM entidades WHERE obra_id = %s AND termino = %s",
        (obra_id, termino),
    )
    fila = cur.fetchone()
    if fila:
        return fila[0]
    cur.execute(
        """INSERT INTO entidades (obra_id, termino, categoria, alias, variantes, confianza)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (obra_id, termino, categoria, alias or [], variantes or [], confianza),
    )
    return cur.fetchone()[0]


def cargar_glosario(cur, obra_id: int, directorio_exports: Path) -> None:
    """Carga personajes/lugares/glosario calibrados (Fase 0b) como
    entidades. Idempotente: obtener_o_crear_entidad no duplica si el
    término ya existe, así que este script se puede relanzar sin miedo
    tras una nueva pasada de calibración."""
    ficheros = [
        ("personajes_obra.json", "persona"),
        ("lugares_obra.json", "lugar"),
        ("glosario_obra.json", None),  # la categoría viene del propio registro
    ]
    total = 0
    for nombre, categoria_por_defecto in ficheros:
        ruta = directorio_exports / nombre
        if not ruta.exists():
            print(f"  Aviso: no se encontró {ruta}; sigo sin esas entidades.")
            continue
        entradas = json.loads(ruta.read_text(encoding="utf-8"))
        for entrada in entradas:
            categoria = entrada.get("categoria") or categoria_por_defecto
            obtener_o_crear_entidad(
                cur, obra_id,
                termino=entrada["termino"],
                categoria=categoria,
                alias=entrada.get("alias"),
                variantes=entrada.get("variantes"),
                confianza=entrada.get("confianza"),
            )
            total += 1
    print(f"  {total} entidades cargadas desde el glosario calibrado.")


def indexar_menciones(
    cur, tabla: str, columna_fk: str, id_padre: int,
    obra_id: int, nombres: list[str],
) -> None:
    """Vincula un fragmento con las entidades que menciona, por
    coincidencia exacta de término canónico. Heurística v1: no resuelve
    variantes que el glosario no haya registrado como tales todavía.
    Suficiente para no bloquear el resto del Almacén — revisar
    empíricamente si hace falta un matching más laxo."""
    for nombre in nombres or []:
        cur.execute(
            "SELECT id FROM entidades WHERE obra_id = %s AND termino = %s",
            (obra_id, nombre),
        )
        fila = cur.fetchone()
        if not fila:
            continue
        cur.execute(
            f"""INSERT INTO {tabla} ({columna_fk}, entidad_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING""",
            (id_padre, fila[0]),
        )


def cargar_fragmento(
    cur, modelo: SentenceTransformer, obra_id: int,
    canon_json: dict, orden_fragmento: int,
) -> int:
    ruta_origen = canon_json.get("ruta_fragmento_origen")
    if not ruta_origen or not Path(ruta_origen).exists():
        raise FileNotFoundError(
            f"canon fragmento {orden_fragmento}: 'ruta_fragmento_origen' ausente "
            f"o no encontrada ({ruta_origen}). Sin el texto no se puede vectorizar."
        )
    texto = Path(ruta_origen).read_text(encoding="utf-8")
    embedding = embed_pasaje(modelo, texto)

    cur.execute(
        """INSERT INTO fragmentos
               (obra_id, orden_fragmento, tipo_documental, texto, embedding, ruta_origen)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (obra_id, orden_fragmento) DO UPDATE
               SET tipo_documental = EXCLUDED.tipo_documental,
                   texto           = EXCLUDED.texto,
                   embedding       = EXCLUDED.embedding,
                   ruta_origen     = EXCLUDED.ruta_origen
           RETURNING id""",
        (obra_id, orden_fragmento, canon_json.get("tipo_documental"),
         texto, embedding, ruta_origen),
    )
    fragmento_id = cur.fetchone()[0]

    menciones = (
        (canon_json.get("personajes") or [])
        + (canon_json.get("lugares") or [])
        + (canon_json.get("organizaciones") or [])
        + (canon_json.get("objetos") or [])
    )
    indexar_menciones(cur, "fragmento_entidad", "fragmento_id", fragmento_id, obra_id, menciones)

    return fragmento_id


def cargar_hechos(
    cur, obra_id: int, fragmento_id: int, orden_fragmento: int, hechos: list[dict],
) -> None:
    for indice, hecho in enumerate(hechos):
        atribucion = hecho.get("atribucion", {})
        atribucion_tipo = atribucion.get("tipo")
        personaje_nombre = atribucion.get("personaje")

        atribucion_personaje_id = None
        if personaje_nombre:
            cur.execute(
                "SELECT id FROM entidades WHERE obra_id = %s AND termino = %s",
                (obra_id, personaje_nombre),
            )
            fila = cur.fetchone()
            atribucion_personaje_id = fila[0] if fila else None

        posicion_global = orden_fragmento * 10000 + indice

        cur.execute(
            """INSERT INTO hechos_canonicos
                   (obra_id, fragmento_id, orden_en_fragmento, posicion_global,
                    hecho, momento_canonico, atribucion_tipo, atribucion_personaje_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (obra_id, fragmento_id, indice, posicion_global,
             hecho["hecho"], hecho["momento_canonico"], atribucion_tipo,
             atribucion_personaje_id),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--obra", required=True, help="slug de la obra, p.ej. 'sp'")
    parser.add_argument(
        "--exports", required=True,
        help="directorio data/<obra>/exports producido por la Fase 0/1",
    )
    parser.add_argument("--dsn", default=DSN_DEFAULT)
    args = parser.parse_args()

    directorio_exports = Path(args.exports)
    directorio_canon = directorio_exports / "canon"
    if not directorio_canon.exists():
        raise SystemExit(
            f"No existe {directorio_canon}. Este script espera un JSON por "
            f"fragmento ahí dentro (ver prerrequisito en el docstring), no "
            f"el canon_fragmento.json único que produce extraer_canon.py hoy."
        )

    print("Cargando modelo de embeddings (puede tardar la primera vez)...")
    modelo = cargar_modelo()

    conexion = psycopg2.connect(args.dsn)
    conexion.autocommit = False
    try:
        with conexion.cursor() as cur:
            obra_id = obtener_o_crear_obra(cur, args.obra)

            print("Cargando glosario calibrado (Fase 0b)...")
            cargar_glosario(cur, obra_id, directorio_exports)

            ficheros_canon = sorted(directorio_canon.glob("*.json"))
            print(f"Cargando {len(ficheros_canon)} fragmentos de canon (Fase 1)...")
            for ruta in ficheros_canon:
                canon_json = json.loads(ruta.read_text(encoding="utf-8"))
                orden_fragmento = canon_json.get("orden_fragmento")
                if orden_fragmento is None:
                    raise ValueError(f"{ruta} no trae 'orden_fragmento'.")

                fragmento_id = cargar_fragmento(cur, modelo, obra_id, canon_json, orden_fragmento)
                cargar_hechos(
                    cur, obra_id, fragmento_id, orden_fragmento,
                    canon_json.get("hechos_canonicos", []),
                )
                print(f"  fragmento {orden_fragmento}: {ruta.name} cargado.")

        conexion.commit()
        print("Almacén poblado.")
    except Exception:
        conexion.rollback()
        raise
    finally:
        conexion.close()


if __name__ == "__main__":
    main()
