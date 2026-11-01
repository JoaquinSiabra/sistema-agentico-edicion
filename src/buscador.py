"""
buscador.py

Las vías de recuperación de hechos y su fusión, comunes a los dos
buscadores del sistema:
  - el de Continuidad (continuidad.reunir_contexto), para el AUTOR: todo
    el canon, anterior y posterior al punto de inserción;
  - el del lector (recuperar.consultar), con filtro anti-spoiler.

Vías (docs/buscador_hibrido.md):
  - "entidades": las entidades que nombra el texto (`entidad: <término>`);
  - "entorno": las que nombra el párrafo anterior al punto y no el texto
    (`entorno: <término>`), para el sujeto implícito;
  - "lexica": texto completo de Postgres sobre los hechos (`lexica`).
Cada vía da una lista ordenada por origen; las listas se fusionan por rango
recíproco (RRF) y el tope se aplica después.

LÍMITE — cada vía acepta `hasta`: el último fragmento cuyos hechos se
admiten. Sin él (Continuidad) entra todo el canon; con él (el lector) la
condición va en el WHERE de la consulta, nunca como filtro posterior: lo
que no se ha leído no sale nunca de la base de datos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from recuperar import Hecho, _filtro_entidad

TOPE_HECHOS = 60   # medido en el banco (docs/buscador_hibrido.md): v1 usaba 200
RRF_K = 60
MAXIMO_LEXICA = 30
CARACTERES_ENTORNO = 1500
NARRADOR = "narrador_documental"


@dataclass
class HechoContexto:
    id: int                   # posicion_global
    orden_fragmento: int
    hecho: str
    momento_canonico: str
    atribucion_tipo: str
    atribuido_a: str | None
    direccion: str            # anterior | posterior | mismo_fragmento
    origen: list[str]         # vías que lo trajeron, p.ej. ["entidad: Menelao"]
    puesto: int | None = None  # en el ranking fusionado (1 = el mejor)

    @property
    def del_narrador(self) -> bool:
        return self.atribucion_tipo == NARRADOR

    @property
    def afirmado_por(self) -> str:
        if self.del_narrador:
            return "narrador"
        return self.atribuido_a or "personaje sin identificar"


def formas(entidad: dict) -> set[str]:
    """Formas por las que se detecta una entidad. Nunca los epítetos (no
    están en la base de datos y, en los JSON del glosario, se ignoran): un
    «domador de caballos» no identifica a nadie."""
    return {entidad["termino"], *(entidad.get("alias") or []),
            *(entidad.get("variantes") or [])} - {""}


def detectar_entidades(pasaje: str, entidades: list[dict]) -> list[str]:
    """Términos de las entidades que el pasaje nombra por su término, un
    alias o una variante, como palabra completa y sin distinguir mayúsculas."""
    detectadas = []
    for entidad in entidades:
        if any(re.search(rf"(?<!\w){re.escape(f)}(?!\w)", pasaje, re.IGNORECASE)
               for f in formas(entidad)):
            detectadas.append(entidad["termino"])
    return sorted(detectadas)


def entidades_de_obra(cur, obra: str) -> list[dict]:
    cur.execute(
        """SELECT e.termino, e.alias, e.variantes FROM entidades e
           JOIN obras o ON o.id = e.obra_id WHERE o.slug = %s ORDER BY e.termino""",
        (obra,),
    )
    return [{"termino": t, "alias": a, "variantes": v} for t, a, v in cur.fetchall()]


def _limite(hasta: int | None) -> tuple[str, list]:
    if hasta is None:
        return "", []
    return " AND f.orden_fragmento <= %s", [hasta]


def hechos_de_entidad(cur, obra: str, entidad: str, hasta: int | None = None) -> list[Hecho]:
    """Como recuperar.hechos_hasta(entidad=...): sin `hasta`, en todo el canon."""
    filtro, params = _filtro_entidad(entidad)
    limite, params_limite = _limite(hasta)
    cur.execute(
        f"""SELECT f.orden_fragmento, h.orden_en_fragmento, h.hecho,
                   h.momento_canonico, h.atribucion_tipo, a.termino
            FROM hechos_canonicos h
            JOIN fragmentos f ON f.id = h.fragmento_id
            JOIN obras o ON o.id = h.obra_id
            LEFT JOIN entidades a ON a.id = h.atribucion_personaje_id
            WHERE o.slug = %s AND {filtro}{limite}
            ORDER BY h.posicion_global""",
        [obra, *params, *params_limite],
    )
    return [Hecho(*fila) for fila in cur.fetchall()]


def posicion_global(hecho: Hecho) -> int:
    return hecho.orden_fragmento * 10000 + hecho.orden_en_fragmento


def ordenar_por_cercania(hechos: list[Hecho], orden: int) -> list[Hecho]:
    """Del más cercano al punto de inserción al más lejano, por distancia en
    posicion_global, tomando como punto el comienzo del fragmento de
    inserción (los hechos no tienen posición en caracteres)."""
    punto = orden * 10000
    return sorted(hechos, key=lambda h: (abs(posicion_global(h) - punto), posicion_global(h)))


def hechos_de_entidades(cur, obra: str, terminos: list[str], prefijo: str,
                        orden: int, hasta: int | None = None) -> dict[str, list[Hecho]]:
    return {f"{prefijo}: {t}": ordenar_por_cercania(hechos_de_entidad(cur, obra, t, hasta), orden)
            for t in terminos}


def parrafo_anterior(texto: str, char: int) -> str:
    """El párrafo (bloque separado por línea en blanco) que acaba antes del
    punto de inserción: si el punto está a mitad de párrafo, su parte ya
    escrita; si está al comienzo de uno, el párrafo de antes. Si antes del
    punto no hay ninguna línea en blanco, los CARACTERES_ENTORNO anteriores."""
    antes = texto[:char].rstrip()
    separadores = list(re.finditer(r"\n[ \t]*\n", antes))
    if not separadores:
        return antes[-CARACTERES_ENTORNO:]
    return antes[separadores[-1].end():].strip()


def consulta_lexica(pasaje: str) -> str | None:
    """Las palabras del pasaje, sin repetir, unidas con OR para
    to_tsquery('spanish', ...). Las vacías las descarta la propia
    configuración `spanish` de Postgres. Solo letras: ningún operador de
    tsquery llega a la consulta. None si el pasaje no tiene palabras."""
    palabras = list(dict.fromkeys(p.lower() for p in re.findall(r"[^\W\d_]+", pasaje)))
    return " | ".join(palabras) or None


def via_entidades(cur, obra: str, pasaje: str, orden: int, texto: str,
                  char: int, hasta: int | None = None) -> dict[str, list[Hecho]]:
    detectadas = detectar_entidades(pasaje, entidades_de_obra(cur, obra))
    return hechos_de_entidades(cur, obra, detectadas, "entidad", orden, hasta)


def via_entorno(cur, obra: str, pasaje: str, orden: int, texto: str,
                char: int, hasta: int | None = None) -> dict[str, list[Hecho]]:
    """Entidades del párrafo anterior que el pasaje no nombra: el sujeto
    implícito («Y blandía la espada…» tras un párrafo sobre Menelao)."""
    entidades = entidades_de_obra(cur, obra)
    en_pasaje = set(detectar_entidades(pasaje, entidades))
    en_entorno = [t for t in detectar_entidades(parrafo_anterior(texto, char), entidades)
                  if t not in en_pasaje]
    return hechos_de_entidades(cur, obra, en_entorno, "entorno", orden, hasta)


def via_lexica(cur, obra: str, pasaje: str, orden: int, texto: str,
               char: int, hasta: int | None = None) -> dict[str, list[Hecho]]:
    consulta = consulta_lexica(pasaje)
    if consulta is None:
        return {}
    limite, params_limite = _limite(hasta)
    # Misma expresión que el índice GIN de schema.sql (hechos_texto_idx).
    cur.execute(
        f"""SELECT f.orden_fragmento, h.orden_en_fragmento, h.hecho,
                  h.momento_canonico, h.atribucion_tipo, a.termino
           FROM hechos_canonicos h
           JOIN fragmentos f ON f.id = h.fragmento_id
           JOIN obras o ON o.id = h.obra_id
           LEFT JOIN entidades a ON a.id = h.atribucion_personaje_id,
                to_tsquery('spanish', %s) AS q
           WHERE o.slug = %s AND to_tsvector('spanish', h.hecho) @@ q{limite}
           ORDER BY ts_rank_cd(to_tsvector('spanish', h.hecho), q) DESC, h.posicion_global
           LIMIT %s""",
        (consulta, obra, *params_limite, MAXIMO_LEXICA),
    )
    return {"lexica": [Hecho(*fila) for fila in cur.fetchall()]}


# Cada vía: (cur, obra, pasaje, orden, texto del fragmento, char, hasta=None)
# -> {origen: hechos, en orden de relevancia}.
VIAS = {"entidades": via_entidades, "entorno": via_entorno, "lexica": via_lexica}
# Por defecto, entidades + léxica: en el banco cubre 16/16 con el hecho
# esperado como mucho en el puesto 13; la vía de entorno mete ruido
# (docs/buscador_hibrido.md, «Resultado de la medida»).
VIAS_V1 = ("entidades", "lexica")


def direccion(orden_hecho: int, orden: int) -> str:
    if orden_hecho < orden:
        return "anterior"
    if orden_hecho > orden:
        return "posterior"
    return "mismo_fragmento"


def fusionar(por_origen: dict[str, list[Hecho]]) -> list[int]:
    """Fusión por rango recíproco: cada hecho suma 1 / (RRF_K + puesto) por
    cada lista en que aparece (puesto 1 = el primero). Devuelve los
    identificadores de mayor a menor puntuación; a igualdad, en orden de
    lectura."""
    puntos: dict[int, Fraction] = {}
    for hechos in por_origen.values():
        vistos: set[int] = set()
        for h in hechos:
            if posicion_global(h) in vistos:
                continue
            vistos.add(posicion_global(h))
            puntos[posicion_global(h)] = puntos.get(posicion_global(h), Fraction(0)) \
                + Fraction(1, RRF_K + len(vistos))
    return sorted(puntos, key=lambda i: (-puntos[i], i))


def combinar(
    por_origen: dict[str, list[Hecho]], orden: int, tope: int = TOPE_HECHOS,
) -> tuple[list[HechoContexto], int]:
    """Une los hechos de todas las vías, sin repetir, con su dirección, su
    origen y su puesto en la fusión. Entran los `tope` mejores de la fusión.
    Devuelve los hechos, en orden de lectura, y cuántos se omitieron."""
    if tope < 0:
        # Con un tope negativo, el corte de la lista dejaría pasar casi todo.
        raise ValueError(f"El tope no puede ser negativo: {tope}.")
    origenes: dict[int, list[str]] = {}
    unicos: dict[int, Hecho] = {}
    for origen, hechos in por_origen.items():
        for h in hechos:
            unicos[posicion_global(h)] = h
            origenes.setdefault(posicion_global(h), [])
            if origen not in origenes[posicion_global(h)]:
                origenes[posicion_global(h)].append(origen)

    ranking = fusionar(por_origen)
    puestos = {i: n for n, i in enumerate(ranking, start=1)}
    hechos = [
        HechoContexto(
            id=i, orden_fragmento=unicos[i].orden_fragmento, hecho=unicos[i].hecho,
            momento_canonico=unicos[i].momento_canonico,
            atribucion_tipo=unicos[i].atribucion_tipo, atribuido_a=unicos[i].atribuido_a,
            direccion=direccion(unicos[i].orden_fragmento, orden),
            origen=sorted(origenes[i]), puesto=puestos[i],
        )
        for i in sorted(ranking[:tope])
    ]
    return hechos, len(unicos) - len(hechos)


def texto_fragmento(cur, obra: str, orden: int) -> str:
    cur.execute(
        """SELECT f.texto FROM fragmentos f JOIN obras o ON o.id = f.obra_id
           WHERE o.slug = %s AND f.orden_fragmento = %s""",
        (obra, orden),
    )
    fila = cur.fetchone()
    if not fila:
        raise ValueError(f"La obra {obra!r} no tiene fragmento {orden}.")
    return fila[0]


def ultimo_fragmento(cur, obra: str) -> int:
    cur.execute(
        """SELECT max(f.orden_fragmento) FROM fragmentos f
           JOIN obras o ON o.id = f.obra_id WHERE o.slug = %s""",
        (obra,),
    )
    return cur.fetchone()[0]
