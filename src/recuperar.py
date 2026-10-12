"""
recuperar.py

Retriever del Almacén con filtro anti-spoiler: dado un punto de lectura,
devuelve solo lo que el lector ya ha leído.

  (a) buscar_pasajes: búsqueda semántica sobre `pasajes` (e5, prefijo
      "query: ", distancia coseno).
  (b) hechos_hasta: fichas canónicas de `hechos_canonicos`, opcionalmente
      solo las que mencionan una entidad (término canónico, alias o
      variantes del glosario).

PUNTO DE LECTURA = (hasta_fragmento, hasta_char):
  - hasta_char=None  -> el fragmento `hasta_fragmento` está leído entero.
  - hasta_char=N     -> del fragmento `hasta_fragmento` solo se han leído
                        los N primeros caracteres de fragmentos.texto.

Dentro de un fragmento, el orden de pasajes y el de hechos NO son
comparables (ver schema.sql): los pasajes tienen inicio_char/fin_char,
los hechos solo un índice aproximado. Por eso, con lectura parcial:
  - pasajes: entran los que terminan antes de hasta_char (fin_char <= N);
  - hechos:  NO entra ninguno del fragmento en curso. Es conservador
             (se pierden hechos ya leídos), pero no filtra nada posterior.

El filtro va en el WHERE de SQL, nunca como post-filtro en Python: así
lo que no se ha leído no sale nunca de la base de datos.

Uso:
    python src/recuperar.py --obra=iliada --hasta-fragmento=1 "consulta"
    python src/recuperar.py --obra=iliada --hasta-fragmento=2 --hasta-char=5000 \
        --entidad=Aquiles "consulta"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psycopg2

if TYPE_CHECKING:
    # Import diferido: sentence_transformers tarda ~10 s en cargar y
    # solo hace falta para el modelo, no para trocear ni filtrar.
    from sentence_transformers import SentenceTransformer

from poblar_almacen import DSN_DEFAULT, MODELO_EMBEDDING


@dataclass
class Pasaje:
    orden_fragmento: int
    orden_en_fragmento: int
    inicio_char: int
    fin_char: int
    texto: str
    similitud: float


@dataclass
class Hecho:
    orden_fragmento: int
    orden_en_fragmento: int
    hecho: str
    momento_canonico: str
    atribucion_tipo: str
    atribuido_a: str | None


def cargar_modelo() -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODELO_EMBEDDING)


def embed_consulta(modelo: SentenceTransformer, consulta: str) -> list[float]:
    # Prefijo "query: " (no "passage: "): ver embed_pasajes en poblar_almacen.py.
    return modelo.encode(f"query: {consulta}", normalize_embeddings=True).tolist()


def _filtro_pasajes(hasta_fragmento: int, hasta_char: int | None) -> tuple[str, list]:
    if hasta_char is None:
        return "f.orden_fragmento <= %s", [hasta_fragmento]
    return (
        "(f.orden_fragmento < %s OR (f.orden_fragmento = %s AND p.fin_char <= %s))",
        [hasta_fragmento, hasta_fragmento, hasta_char],
    )


def buscar_pasajes(
    cur,
    modelo: SentenceTransformer,
    obra: str,
    consulta: str,
    hasta_fragmento: int,
    hasta_char: int | None = None,
    k: int = 5,
) -> list[Pasaje]:
    # Con un WHERE, HNSW por defecto filtra DESPUÉS de sacar ef_search
    # candidatos y puede devolver menos de k filas. El scan iterativo
    # (pgvector >= 0.8) sigue buscando hasta llenar k.
    cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
    vector = str(embed_consulta(modelo, consulta))
    filtro, params = _filtro_pasajes(hasta_fragmento, hasta_char)
    cur.execute(
        f"""SELECT f.orden_fragmento, p.orden_en_fragmento, p.inicio_char,
                   p.fin_char, p.texto, 1 - (p.embedding <=> %s::vector)
            FROM pasajes p
            JOIN fragmentos f ON f.id = p.fragmento_id
            JOIN obras o ON o.id = p.obra_id
            WHERE o.slug = %s AND {filtro}
            ORDER BY p.embedding <=> %s::vector
            LIMIT %s""",
        [vector, obra, *params, vector, k],
    )
    return [Pasaje(*fila) for fila in cur.fetchall()]


def hechos_hasta(
    cur,
    obra: str,
    hasta_fragmento: int,
    hasta_char: int | None = None,
    entidad: str | None = None,
) -> list[Hecho]:
    # Lectura parcial: el fragmento en curso no aporta hechos (ver docstring).
    ultimo_completo = hasta_fragmento if hasta_char is None else hasta_fragmento - 1
    condiciones = ["o.slug = %s", "f.orden_fragmento <= %s"]
    params: list = [obra, ultimo_completo]
    if entidad is not None:
        # Cualquier forma registrada de la entidad (término, alias,
        # variantes) que aparezca en el texto del hecho o sea quien lo afirma.
        condiciones.append(
            """EXISTS (
                 SELECT 1 FROM entidades e,
                      unnest(array_append(e.alias || e.variantes, e.termino)) AS forma
                 WHERE e.obra_id = o.id
                   AND (e.termino ILIKE %s OR %s ILIKE ANY (e.alias))
                   AND (h.hecho ILIKE '%%' || forma || '%%'
                        OR h.atribucion_personaje_id = e.id))"""
        )
        params += [entidad, entidad]
    cur.execute(
        f"""SELECT f.orden_fragmento, h.orden_en_fragmento, h.hecho,
                   h.momento_canonico, h.atribucion_tipo, a.termino
            FROM hechos_canonicos h
            JOIN fragmentos f ON f.id = h.fragmento_id
            JOIN obras o ON o.id = h.obra_id
            LEFT JOIN entidades a ON a.id = h.atribucion_personaje_id
            WHERE {' AND '.join(condiciones)}
            ORDER BY h.posicion_global""",
        params,
    )
    return [Hecho(*fila) for fila in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("consulta")
    parser.add_argument("--obra", required=True)
    parser.add_argument("--hasta-fragmento", type=int, required=True)
    parser.add_argument("--hasta-char", type=int, default=None)
    parser.add_argument("--entidad", default=None,
                        help="si se da, lista también los hechos canónicos de esa entidad")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--dsn", default=DSN_DEFAULT)
    args = parser.parse_args()

    modelo = cargar_modelo()
    with psycopg2.connect(args.dsn) as con, con.cursor() as cur:
        for p in buscar_pasajes(cur, modelo, args.obra, args.consulta,
                                args.hasta_fragmento, args.hasta_char, args.k):
            print(f"[{p.orden_fragmento}#{p.orden_en_fragmento} "
                  f"{p.inicio_char}-{p.fin_char} sim={p.similitud:.3f}] "
                  f"{p.texto[:200]}…\n")
        if args.entidad:
            print(f"--- Hechos canónicos sobre {args.entidad}:")
            for h in hechos_hasta(cur, args.obra, args.hasta_fragmento,
                                  args.hasta_char, args.entidad):
                quien = f" ({h.atribuido_a})" if h.atribuido_a else ""
                print(f"  [{h.orden_fragmento}#{h.orden_en_fragmento}] "
                      f"{h.hecho}{quien}")


if __name__ == "__main__":
    main()
