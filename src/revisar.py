"""
revisar.py

Informe para el AUTOR: pasa un pasaje nuevo por los agentes que trabajan
para él y reúne sus informes en uno. Hoy son dos:
  - continuidad: lo que el pasaje contradice del canon (docs/agente_continuidad.md);
  - voz: normas de la edición y léxico ajeno a la obra (docs/agente_voz.md).

No añade criterio propio: cada agente decide y valida lo suyo, y aquí solo
se ejecutan en orden y se presentan juntos. Si un agente falla (la API,
el JSON de la respuesta), el informe lo dice y los demás siguen.

Los agentes son intercambiables, como las vías del buscador: AGENTES dice
cuáles hay y --agentes cuáles se ejecutan.

Uso:
    python src/revisar.py --obra=iliada --orden=4 --char=12345 --fichero=pasaje.txt
    python src/revisar.py --obra=iliada --orden=4 --char=12345 --fichero=pasaje.txt \
        --agentes=voz --json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psycopg2

import continuidad
import voz
from poblar_almacen import DSN_DEFAULT


@dataclass
class Revision:
    informes: dict[str, object] = field(default_factory=dict)   # agente -> Informe
    errores: dict[str, str] = field(default_factory=dict)       # agente -> error

    @property
    def avisos(self) -> int:
        return sum(len(i.avisos) for i in self.informes.values())

    def a_dict(self) -> dict:
        return {"avisos": self.avisos,
                "informes": {a: asdict(i) for a, i in self.informes.items()},
                "errores": self.errores}


def _continuidad(cur, obra, pasaje, orden, char, cliente, modelo_embeddings):
    return continuidad.evaluar(cur, obra, pasaje, orden, char, modelo_embeddings, cliente)


def _voz(cur, obra, pasaje, orden, char, cliente, modelo_embeddings):
    return voz.evaluar(cur, obra, pasaje, orden, char, cliente)


# Cada agente: (cur, obra, pasaje, orden, char, cliente, modelo_embeddings)
# -> su Informe; y cómo se imprime. El orden es el del informe.
AGENTES = {
    "continuidad": (_continuidad, continuidad.imprimir),
    "voz": (_voz, voz.imprimir),
}
TITULOS = {"continuidad": "Continuidad", "voz": "Voz y edición"}


def revisar(cur, obra: str, pasaje: str, orden: int, char: int,
            agentes: tuple[str, ...] = tuple(AGENTES), cliente=None,
            modelo_embeddings=None) -> Revision:
    desconocidos = [a for a in agentes if a not in AGENTES]
    if desconocidos:
        raise ValueError(f"Agentes desconocidos: {desconocidos}; hay {list(AGENTES)}.")
    revision = Revision()
    for agente in (a for a in AGENTES if a in agentes):
        evaluar, _ = AGENTES[agente]
        try:
            revision.informes[agente] = evaluar(cur, obra, pasaje, orden, char,
                                                cliente, modelo_embeddings)
        except json.JSONDecodeError as e:   # la respuesta del modelo: los demás siguen
            revision.errores[agente] = f"{type(e).__name__}: {e}"
        except ValueError:
            raise   # posición mal dada o fragmento inexistente: error de la petición
        except Exception as e:   # la API: los demás siguen
            revision.errores[agente] = f"{type(e).__name__}: {e}"
    return revision


def imprimir(revision: Revision) -> None:
    for agente in AGENTES:
        if agente not in revision.informes and agente not in revision.errores:
            continue
        print(f"=== {TITULOS[agente]} ===")
        if agente in revision.errores:
            print(f"No se pudo evaluar: {revision.errores[agente]}")
        else:
            AGENTES[agente][1](revision.informes[agente])
        print()
    resumen = f"{revision.avisos} avisos en total"
    if revision.errores:
        resumen += f"; {len(revision.errores)} agentes no se pudieron evaluar"
    print(resumen + ".")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--obra", required=True)
    parser.add_argument("--orden", type=int, required=True,
                        help="fragmento donde se inserta el pasaje")
    parser.add_argument("--char", type=int, required=True,
                        help="carácter de inserción dentro de ese fragmento")
    parser.add_argument("--fichero", required=True, help="texto del pasaje nuevo")
    parser.add_argument("--agentes", default=",".join(AGENTES),
                        help=f"agentes, separados por comas ({', '.join(AGENTES)})")
    parser.add_argument("--json", action="store_true", help="imprimir el informe en JSON")
    parser.add_argument("--dsn", default=DSN_DEFAULT)
    args = parser.parse_args()

    agentes = tuple(args.agentes.split(","))
    pasaje = Path(args.fichero).read_text(encoding="utf-8")
    # Los pasajes parecidos de Continuidad necesitan el modelo de embeddings.
    modelo = continuidad.cargar_modelo() if "continuidad" in agentes else None
    with psycopg2.connect(args.dsn) as con, con.cursor() as cur:
        revision = revisar(cur, args.obra, pasaje, args.orden, args.char, agentes,
                           modelo_embeddings=modelo)
    if args.json:
        print(json.dumps(revision.a_dict(), ensure_ascii=False, indent=2))
    else:
        imprimir(revision)
    if revision.errores:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
