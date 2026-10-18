"""
continuidad.py

Agente de Continuidad (v1): herramienta del AUTOR. Dado un pasaje que se va
a insertar en la obra y su posición, avisa de lo que contradice el canon ya
establecido. No corrige estilo ni opina sobre la trama. Especificación en
docs/agente_continuidad.md.

Tres pasos:
  1. reunir_contexto (determinista, base de datos): las entidades que nombra
     el pasaje, sus hechos en TODO el canon (anterior y posterior: al autor
     le importa también lo que viene después; aquí no hay filtro
     anti-spoiler) con su dirección, y los pasajes más parecidos.
  2. consultar_modelo (una llamada a Claude): avisos y notas en JSON.
  3. validar (determinista, sin base de datos ni API): descarta o corrige
     lo que no se sostiene y devuelve el Informe.

VÍAS DE RECUPERACIÓN — reunir_contexto recibe qué vías usar para traer
hechos (en v1, solo "entidades"), y cada hecho del contexto guarda por qué
vía o vías llegó (`entidad: <término>`). Así se pueden comparar
combinaciones de vías sobre el mismo banco sin tocar el resto del agente.

ATRIBUCIÓN — solo un hecho del narrador (`narrador_documental`) puede
fundar un aviso. Lo que afirma un personaje puede ser falso: como mucho da
una nota.

Uso:
    python src/continuidad.py --obra=iliada --orden=4 --char=12345 --fichero=pasaje.txt
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic
import psycopg2

from calibrar_glosario import MODELO
from poblar_almacen import DSN_DEFAULT
from recuperar import Hecho, Pasaje, _filtro_entidad, buscar_pasajes, cargar_modelo

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

TOPE_HECHOS = 200
PASAJES_PARECIDOS = 5
CARACTERES_ENTORNO = 1500
NARRADOR = "narrador_documental"

TIPOS = {"hecho", "estado"}
GRAVEDADES = {"contradiccion", "tension"}
DIRECCIONES = {"anterior", "posterior"}

PROMPT_SISTEMA = """Eres el revisor de continuidad de una obra narrativa. El \
autor va a insertar un pasaje nuevo en un punto de la obra y quiere saber si \
contradice el canon ya establecido: hechos y estado de personajes y objetos \
(vivo o muerto, herido, dónde está, qué tiene, qué sabe). No corriges estilo, \
no opinas sobre la trama ni sobre cómo hablaría un personaje.

Recibes los hechos canónicos de la obra que tienen que ver con el pasaje, \
cada uno con su identificador entre corchetes, quién lo afirma y su dirección \
respecto al punto de inserción (anterior o posterior en la obra).

Reglas:
- Un aviso SOLO puede apoyarse en un hecho marcado como NARRADOR.
- Lo que dice un personaje (en el canon o en el pasaje) puede ser falso o \
interesado: mentiras, jactancias, creencias erróneas, profecías. Si choca con \
algo, va a "notas", nunca a "avisos".
- Gravedad:
  - "contradiccion": el pasaje es imposible tal como está. Ejemplo: el canon \
dice que la casa ardió entera; el pasaje describe a alguien durmiendo en ella, \
intacta, la noche siguiente.
  - "tension": es posible, pero necesita explicación. Ejemplo: el canon dice \
que alguien vive a tres días de camino; el pasaje lo pone en la plaza esa \
misma tarde sin decir cómo llegó.
- Tipo: "hecho" si contradice algo que ocurrió; "estado" si contradice el \
estado de un personaje o un objeto.
- Dirección: "anterior" si el hecho está antes del punto de inserción, \
"posterior" si está después. Para los hechos marcados "mismo_fragmento", \
decídelo tú con el texto que rodea al punto de inserción.
- La "cita" es un fragmento LITERAL del pasaje nuevo, copiado tal cual.
- "hecho" es el identificador numérico de un hecho de la lista. Los pasajes \
parecidos son solo contexto: nunca se citan como prueba.
- Si no hay nada, devuelve listas vacías: no hay que encontrar algo a la fuerza.

Devuelve EXCLUSIVAMENTE este JSON, sin texto antes ni después:
{
  "avisos": [
    {
      "cita": "fragmento literal del pasaje",
      "hecho": 40021,
      "tipo": "hecho | estado",
      "gravedad": "contradiccion | tension",
      "direccion": "anterior | posterior",
      "explicacion": "una o dos frases"
    }
  ],
  "notas": [
    {"cita": "…", "hecho": 30029, "explicacion": "…"}
  ]
}
"""


# --- Datos -------------------------------------------------------------------

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

    @property
    def del_narrador(self) -> bool:
        return self.atribucion_tipo == NARRADOR

    @property
    def afirmado_por(self) -> str:
        if self.del_narrador:
            return "narrador"
        return self.atribuido_a or "personaje sin identificar"


@dataclass
class Contexto:
    obra: str
    orden: int
    char: int
    hechos: list[HechoContexto]
    # Hechos encontrados por cada vía antes del tope (una entidad detectada
    # sin hechos aparece con 0): es lo que mide la cobertura del buscador.
    candidatos: dict[str, int]
    hechos_omitidos: int
    pasajes: list[Pasaje] = field(default_factory=list)
    entorno_antes: str = ""
    entorno_despues: str = ""

    def hecho(self, identificador) -> HechoContexto | None:
        return next((h for h in self.hechos if h.id == identificador), None)

    @classmethod
    def desde_dict(cls, datos: dict) -> Contexto:
        return cls(**{**datos,
                      "hechos": [HechoContexto(**h) for h in datos["hechos"]],
                      "pasajes": [Pasaje(**p) for p in datos.get("pasajes", [])]})


@dataclass
class Aviso:
    cita: str
    hecho: int
    texto_hecho: str
    afirmado_por: str
    tipo: str
    gravedad: str
    direccion: str
    explicacion: str


@dataclass
class Nota:
    cita: str
    hecho: int | None
    texto_hecho: str | None
    afirmado_por: str | None
    explicacion: str


@dataclass
class Informe:
    avisos: list[Aviso]
    notas: list[Nota]
    descartados: list[dict]   # {"motivo": ..., "aviso": <lo que dijo el modelo>}
    hechos_omitidos: int


# --- 1. Reunir el contexto ---------------------------------------------------

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


def hechos_de_entidad(cur, obra: str, entidad: str) -> list[Hecho]:
    """Como recuperar.hechos_hasta(entidad=...), pero en todo el canon."""
    filtro, params = _filtro_entidad(entidad)
    cur.execute(
        f"""SELECT f.orden_fragmento, h.orden_en_fragmento, h.hecho,
                   h.momento_canonico, h.atribucion_tipo, a.termino
            FROM hechos_canonicos h
            JOIN fragmentos f ON f.id = h.fragmento_id
            JOIN obras o ON o.id = h.obra_id
            LEFT JOIN entidades a ON a.id = h.atribucion_personaje_id
            WHERE o.slug = %s AND {filtro}
            ORDER BY h.posicion_global""",
        [obra, *params],
    )
    return [Hecho(*fila) for fila in cur.fetchall()]


def via_entidades(cur, obra: str, pasaje: str) -> dict[str, list[Hecho]]:
    detectadas = detectar_entidades(pasaje, entidades_de_obra(cur, obra))
    return {f"entidad: {t}": hechos_de_entidad(cur, obra, t) for t in detectadas}


# Cada vía: (cur, obra, pasaje) -> {origen: hechos}. Las siguientes
# (léxica, semántica sobre hechos...) se añaden aquí.
VIAS = {"entidades": via_entidades}
VIAS_V1 = ("entidades",)


def posicion_global(hecho: Hecho) -> int:
    return hecho.orden_fragmento * 10000 + hecho.orden_en_fragmento


def direccion(orden_hecho: int, orden: int) -> str:
    if orden_hecho < orden:
        return "anterior"
    if orden_hecho > orden:
        return "posterior"
    return "mismo_fragmento"


def combinar(
    por_origen: dict[str, list[Hecho]], orden: int, tope: int = TOPE_HECHOS,
) -> tuple[list[HechoContexto], int]:
    """Une los hechos de todas las vías, sin repetir, con su dirección y su
    origen. Si pasan del tope, entran primero los de los orígenes con menos
    hechos (las entidades más específicas) hasta llenarlo. Devuelve los
    hechos, en orden de lectura, y cuántos se omitieron."""
    origenes: dict[int, list[str]] = {}
    unicos: dict[int, Hecho] = {}
    for origen, hechos in por_origen.items():
        for h in hechos:
            unicos[posicion_global(h)] = h
            origenes.setdefault(posicion_global(h), [])
            if origen not in origenes[posicion_global(h)]:
                origenes[posicion_global(h)].append(origen)

    elegidos: list[int] = []
    for origen in sorted(por_origen, key=lambda o: (len(por_origen[o]), o)):
        for h in sorted(por_origen[origen], key=posicion_global):
            if len(elegidos) >= tope:
                break
            if posicion_global(h) not in elegidos:
                elegidos.append(posicion_global(h))

    hechos = [
        HechoContexto(
            id=i, orden_fragmento=unicos[i].orden_fragmento, hecho=unicos[i].hecho,
            momento_canonico=unicos[i].momento_canonico,
            atribucion_tipo=unicos[i].atribucion_tipo, atribuido_a=unicos[i].atribuido_a,
            direccion=direccion(unicos[i].orden_fragmento, orden),
            origen=sorted(origenes[i]),
        )
        for i in sorted(elegidos)
    ]
    return hechos, len(unicos) - len(elegidos)


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


def reunir_contexto(
    cur,
    obra: str,
    pasaje: str,
    orden: int,
    char: int,
    modelo_embeddings: SentenceTransformer | None = None,
    vias: tuple[str, ...] = VIAS_V1,
    tope: int = TOPE_HECHOS,
) -> Contexto:
    """Sin modelo de embeddings no se buscan pasajes parecidos: basta para
    medir la cobertura de hechos, que no depende de ellos."""
    desconocidas = set(vias) - set(VIAS)
    if desconocidas:
        raise ValueError(f"Vías desconocidas: {sorted(desconocidas)}; hay {sorted(VIAS)}.")
    texto = texto_fragmento(cur, obra, orden)
    if not 0 <= char <= len(texto):
        raise ValueError(f"El fragmento {orden} tiene {len(texto)} caracteres; char={char}.")

    por_origen: dict[str, list[Hecho]] = {}
    for via in vias:
        por_origen.update(VIAS[via](cur, obra, pasaje))
    hechos, omitidos = combinar(por_origen, orden, tope)

    pasajes = []
    if modelo_embeddings is not None:
        # Sin límite de posición: hasta el último fragmento de la obra.
        pasajes = buscar_pasajes(cur, modelo_embeddings, obra, pasaje,
                                 hasta_fragmento=ultimo_fragmento(cur, obra),
                                 k=PASAJES_PARECIDOS)
    return Contexto(
        obra=obra, orden=orden, char=char, hechos=hechos,
        candidatos={o: len(h) for o, h in por_origen.items()},
        hechos_omitidos=omitidos, pasajes=pasajes,
        entorno_antes=texto[max(0, char - CARACTERES_ENTORNO):char],
        entorno_despues=texto[char:char + CARACTERES_ENTORNO],
    )


# --- 2. Consultar al modelo --------------------------------------------------

def linea_hecho(h: HechoContexto) -> str:
    quien = "NARRADOR" if h.del_narrador else f"PERSONAJE: {h.afirmado_por}"
    return f"[{h.id}] ({h.direccion}; {quien}; {h.atribucion_tipo}) {h.hecho}"


def construir_prompt(contexto: Contexto, pasaje: str) -> tuple[str, str]:
    """Prompt de sistema y mensaje del usuario. Función pura."""
    partes = [
        f"PUNTO DE INSERCIÓN: fragmento {contexto.orden}, carácter {contexto.char}.",
        "TEXTO ANTES DEL PUNTO DE INSERCIÓN:\n---\n" + contexto.entorno_antes + "\n---",
        "TEXTO DESPUÉS DEL PUNTO DE INSERCIÓN:\n---\n" + contexto.entorno_despues + "\n---",
        "HECHOS CANÓNICOS ([identificador] (dirección; quién lo afirma; atribución)):\n"
        + ("\n".join(linea_hecho(h) for h in contexto.hechos) or "(ninguno)"),
    ]
    if contexto.pasajes:
        partes.append(
            "PASAJES PARECIDOS DE LA OBRA (solo contexto; no se citan como prueba):\n"
            + "\n\n".join(f"(fragmento {p.orden_fragmento}) {p.texto}" for p in contexto.pasajes))
    partes.append("PASAJE NUEVO:\n---\n" + pasaje + "\n---")
    return PROMPT_SISTEMA, "\n\n".join(partes)


def extraer_json(texto: str) -> str:
    """El JSON de la respuesta. En los casos dudosos el modelo a veces
    razona primero en prosa y deja el JSON al final, en un bloque ```json
    (pasó en el banco con S03 y C02): se toma el último bloque así."""
    bloques = re.findall(r"```(?:json)?\s*\n(.*?)(?:\n```|\Z)", texto, re.S)
    if bloques and not texto.lstrip().startswith("{"):
        return bloques[-1].strip()
    return texto.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


def consultar_modelo(contexto: Contexto, pasaje: str, cliente=None) -> dict:
    cliente = cliente or anthropic.Anthropic()
    sistema, usuario = construir_prompt(contexto, pasaje)
    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=4000,
        system=sistema,
        messages=[{"role": "user", "content": usuario}],
    )
    texto = extraer_json(respuesta.content[0].text)
    try:
        return json.loads(texto)
    except json.JSONDecodeError as e:
        print(f"\n--- ERROR AL PARSEAR JSON: {e} ---")
        print(f"stop_reason: {respuesta.stop_reason}")
        print(texto)
        raise


# --- 3. Validar --------------------------------------------------------------

def normalizar(texto: str) -> str:
    return " ".join(texto.split())


def como_entero(valor) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def validar(respuesta: dict, contexto: Contexto, pasaje: str) -> Informe:
    """Garantías que no dependen del modelo (ver docs/agente_continuidad.md,
    «Validar»). Función pura."""
    avisos: list[Aviso] = []
    notas: list[Nota] = []
    descartados: list[dict] = []
    pasaje_normalizado = normalizar(pasaje)

    for aviso in respuesta.get("avisos") or []:
        if not isinstance(aviso, dict):
            descartados.append({"motivo": "aviso mal formado", "aviso": aviso})
            continue
        cita = aviso.get("cita")
        if not isinstance(cita, str) or not cita.strip() \
                or normalizar(cita) not in pasaje_normalizado:
            descartados.append({"motivo": "cita no literal", "aviso": aviso})
            continue
        hecho = contexto.hecho(como_entero(aviso.get("hecho")))
        if hecho is None:
            descartados.append({"motivo": "hecho fuera del contexto", "aviso": aviso})
            continue
        if not hecho.del_narrador:
            notas.append(Nota(cita=cita, hecho=hecho.id, texto_hecho=hecho.hecho,
                              afirmado_por=hecho.afirmado_por,
                              explicacion=str(aviso.get("explicacion", ""))))
            continue
        sentido = aviso.get("direccion")
        if hecho.direccion != "mismo_fragmento":
            sentido = hecho.direccion
        if aviso.get("tipo") not in TIPOS or aviso.get("gravedad") not in GRAVEDADES \
                or sentido not in DIRECCIONES:
            descartados.append({"motivo": "valor no permitido", "aviso": aviso})
            continue
        avisos.append(Aviso(cita=cita, hecho=hecho.id, texto_hecho=hecho.hecho,
                            afirmado_por=hecho.afirmado_por, tipo=aviso["tipo"],
                            gravedad=aviso["gravedad"], direccion=sentido,
                            explicacion=str(aviso.get("explicacion", ""))))

    for nota in respuesta.get("notas") or []:
        if not isinstance(nota, dict):
            continue
        hecho = contexto.hecho(como_entero(nota.get("hecho")))
        notas.append(Nota(
            cita=str(nota.get("cita", "")),
            hecho=hecho.id if hecho else como_entero(nota.get("hecho")),
            texto_hecho=hecho.hecho if hecho else None,
            afirmado_por=hecho.afirmado_por if hecho else None,
            explicacion=str(nota.get("explicacion", "")),
        ))

    return Informe(avisos=avisos, notas=notas, descartados=descartados,
                   hechos_omitidos=contexto.hechos_omitidos)


def evaluar(
    cur,
    obra: str,
    pasaje: str,
    orden: int,
    char: int,
    modelo_embeddings: SentenceTransformer | None = None,
    cliente=None,
    vias: tuple[str, ...] = VIAS_V1,
) -> Informe:
    contexto = reunir_contexto(cur, obra, pasaje, orden, char, modelo_embeddings, vias)
    return validar(consultar_modelo(contexto, pasaje, cliente), contexto, pasaje)


# --- Línea de comandos -------------------------------------------------------

def imprimir(informe: Informe) -> None:
    if not informe.avisos:
        print("Sin avisos.")
    for a in informe.avisos:
        print(f"AVISO ({a.gravedad}, {a.tipo}, hecho {a.direccion}) [{a.hecho}]")
        print(f"  Pasaje: «{a.cita}»")
        print(f"  Hecho canónico ({a.afirmado_por}): {a.texto_hecho}")
        print(f"  {a.explicacion}\n")
    for n in informe.notas:
        quien = f" ({n.afirmado_por})" if n.afirmado_por else ""
        print(f"NOTA [{n.hecho}]{quien}: «{n.cita}» — {n.explicacion}")
    print(f"\n{len(informe.descartados)} avisos descartados por la validación; "
          f"{informe.hechos_omitidos} hechos omitidos por el tope de {TOPE_HECHOS}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--obra", required=True)
    parser.add_argument("--orden", type=int, required=True,
                        help="fragmento donde se inserta el pasaje")
    parser.add_argument("--char", type=int, required=True,
                        help="carácter de inserción dentro de ese fragmento")
    parser.add_argument("--fichero", required=True, help="texto del pasaje nuevo")
    parser.add_argument("--json", action="store_true", help="imprimir el informe en JSON")
    parser.add_argument("--dsn", default=DSN_DEFAULT)
    args = parser.parse_args()

    pasaje = Path(args.fichero).read_text(encoding="utf-8")
    modelo = cargar_modelo()
    with psycopg2.connect(args.dsn) as con, con.cursor() as cur:
        informe = evaluar(cur, args.obra, pasaje, args.orden, args.char, modelo)
    if args.json:
        print(json.dumps(asdict(informe), ensure_ascii=False, indent=2))
    else:
        imprimir(informe)


if __name__ == "__main__":
    main()
