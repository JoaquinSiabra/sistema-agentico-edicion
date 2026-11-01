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
hechos, y cada hecho del contexto guarda por qué vía o vías llegó. Así se
pueden comparar combinaciones de vías sobre el mismo banco sin tocar el
resto del agente. Buscador híbrido (docs/buscador_hibrido.md):
  - "entidades": las entidades que nombra el pasaje (`entidad: <término>`);
  - "entorno": las que nombra el párrafo anterior al punto de inserción y
    no el pasaje (`entorno: <término>`), para el sujeto implícito;
  - "lexica": texto completo de Postgres sobre los hechos (`lexica`).
Cada vía da una lista ordenada por origen; las listas se fusionan por rango
recíproco (RRF) y el tope se aplica después. Por defecto, "entidades" y
"lexica" con un tope de 60 hechos (medido en el banco). Las vías y la
fusión están en buscador.py, compartidas con el buscador del lector.

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

# Las vías y la fusión viven en buscador.py, compartidas con el buscador del
# lector. Se reexportan aquí (de ahí los noqa) para quien ya las importaba
# de continuidad: las pruebas, voz.py y los bancos.
from buscador import (  # noqa: F401
    CARACTERES_ENTORNO, NARRADOR, TOPE_HECHOS, VIAS, VIAS_V1, HechoContexto, combinar,
    consulta_lexica, detectar_entidades, direccion, entidades_de_obra, formas, fusionar,
    hechos_de_entidad, ordenar_por_cercania, parrafo_anterior, posicion_global,
    texto_fragmento, ultimo_fragmento, via_entidades, via_entorno, via_lexica,
)
from calibrar_glosario import MODELO
from poblar_almacen import DSN_DEFAULT
from recuperar import Hecho, Pasaje, buscar_pasajes, cargar_modelo

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

PASAJES_PARECIDOS = 5

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
        por_origen.update(VIAS[via](cur, obra, pasaje, orden, texto, char))
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
    tope: int = TOPE_HECHOS,
) -> Informe:
    contexto = reunir_contexto(cur, obra, pasaje, orden, char, modelo_embeddings, vias, tope)
    return validar(consultar_modelo(contexto, pasaje, cliente), contexto, pasaje)


# --- Línea de comandos -------------------------------------------------------

def imprimir(informe: Informe, tope: int = TOPE_HECHOS) -> None:
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
          f"{informe.hechos_omitidos} hechos omitidos por el tope de {tope}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--obra", required=True)
    parser.add_argument("--orden", type=int, required=True,
                        help="fragmento donde se inserta el pasaje")
    parser.add_argument("--char", type=int, required=True,
                        help="carácter de inserción dentro de ese fragmento")
    parser.add_argument("--fichero", required=True, help="texto del pasaje nuevo")
    parser.add_argument("--vias", default=",".join(VIAS_V1),
                        help=f"vías de recuperación, separadas por comas ({', '.join(VIAS)})")
    parser.add_argument("--tope", type=int, default=TOPE_HECHOS,
                        help="máximo de hechos en el contexto")
    parser.add_argument("--json", action="store_true", help="imprimir el informe en JSON")
    parser.add_argument("--dsn", default=DSN_DEFAULT)
    args = parser.parse_args()
    if args.tope < 0:
        parser.error(f"--tope no puede ser negativo ({args.tope})")

    pasaje = Path(args.fichero).read_text(encoding="utf-8")
    modelo = cargar_modelo()
    with psycopg2.connect(args.dsn) as con, con.cursor() as cur:
        informe = evaluar(cur, args.obra, pasaje, args.orden, args.char, modelo,
                          vias=tuple(args.vias.split(",")), tope=args.tope)
    if args.json:
        print(json.dumps(asdict(informe), ensure_ascii=False, indent=2))
    else:
        imprimir(informe, args.tope)


if __name__ == "__main__":
    main()
