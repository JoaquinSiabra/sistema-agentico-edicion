"""
voz.py

Agente de Voz y edición (v1): herramienta del AUTOR. Dado un pasaje que se
va a insertar en la obra, avisa de dónde se aparta de la voz de la obra.
Especificación en docs/agente_voz.md.

Solo puede fundar un aviso lo que la obra hace siempre o no hace nunca,
medido sobre la propia obra:
  - NORMAS DE LA EDICIÓN (deterministas, sin modelo): formas que la obra
    escribe siempre de una manera («á» y nunca «a» en la Segalá) y la
    marca con que abre los diálogos («», nunca raya).
  - LÉXICO AJENO A LA OBRA: palabras cuya raíz no aparece en la obra
    (filtro determinista) y que el modelo juzga anacrónicas, coloquiales
    o técnicas para esta obra. El modelo no puede avisar de otra palabra.
Persona, tiempo verbal o apóstrofes no fundan avisos: la Ilíada los varía
(II 484, III 369, IV 127, IV 223). Como mucho dan notas.

Pasos:
  1. perfil_de_textos / perfil_de_voz (determinista): normas y raíces de
     la obra, una vez por obra.
  2. medir (determinista): normas rotas -> avisos; candidatos léxicos.
  3. consultar_modelo: una llamada, solo si hay candidatos.
  4. validar (determinista) -> Informe.

Uso:
    python src/voz.py --obra=iliada --orden=4 --char=12345 --fichero=pasaje.txt
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import anthropic
import snowballstemmer

import psycopg2

from calibrar_glosario import MODELO
from continuidad import CARACTERES_ENTORNO, extraer_json, normalizar, texto_fragmento
from poblar_almacen import DSN_DEFAULT

# Pares de formas variables. La lista dice qué pares mirar, no qué forma
# es la buena: la norma sale de medir la obra.
PARES = (("á", "a"), ("ó", "o"), ("é", "e"), ("fué", "fue"), ("dió", "dio"), ("vió", "vio"))
MINIMO_APARICIONES = 10
PROPORCION_NORMA = 0.99

# Marcas de apertura de diálogo. La raya y las comillas rectas cuentan
# solo al comienzo de línea o tras un espacio: la raya pegada a la palabra
# es un inciso («aves—así»), y la comilla recta tras una letra, la de cierre.
MARCAS = {
    "comillas angulares": r"«",
    "raya de diálogo": r"(?:^|(?<=\s))—",
    "comillas inglesas": r"“|(?:^|(?<=\s))\"",
}

RASGOS = {"anacronismo", "coloquialismo", "tecnicismo"}
GRAVEDADES = ("ruptura", "extrañeza")   # de mayor a menor

PALABRA = re.compile(r"[^\W\d_]+")
VERSO = re.compile(r"^\d+ ")
FIN_DE_FRASE = ".!?¡¿«»:;—\"“\n"

_lematizador = snowballstemmer.stemmer("spanish")

PROMPT_SISTEMA = """Eres el revisor de la voz de una obra narrativa. El autor \
va a insertar un pasaje nuevo y quiere saber si alguna de sus palabras se \
aparta de la voz de la obra. No corriges estilo ni opinas sobre la calidad.

Recibes una lista de CANDIDATOS: palabras del pasaje cuya raíz no aparece en \
ninguna parte de la obra. Que una palabra no aparezca en la obra no basta: \
muchas son correctas para ella (flexiones raras, términos de época). Avisa \
solo de las que son, para esta obra:
- "anacronismo": nombra algo que no existe en su mundo;
- "coloquialismo": de un registro coloquial o moderno impropio de su voz;
- "tecnicismo": de un vocabulario técnico ajeno a ella.

Gravedad:
- "ruptura": no puede estar así. Ejemplo: en una novela del siglo XVIII, un \
personaje mira la hora en el móvil.
- "extrañeza": posible, pero conviene revisarlo. Ejemplo: en la misma novela, \
«estresado», que no es imposible pero suena a otra época.

Reglas:
- Un aviso SOLO puede ser sobre una palabra de la lista de candidatos.
- La "cita" es un fragmento LITERAL del pasaje que contiene la palabra.
- La persona gramatical, el tiempo verbal, los apóstrofes o el registro de \
una construcción con palabras de la obra van a "notas", nunca a "avisos".
- La ortografía y las marcas de diálogo ya se comprueban aparte: no las \
comentes.
- Si no hay nada, listas vacías: no hay que encontrar algo a la fuerza.

Devuelve EXCLUSIVAMENTE este JSON, sin texto antes ni después:
{
  "avisos": [
    {
      "palabra": "candidato tal cual",
      "cita": "fragmento literal del pasaje",
      "rasgo": "anacronismo | coloquialismo | tecnicismo",
      "gravedad": "ruptura | extrañeza",
      "explicacion": "una o dos frases"
    }
  ],
  "notas": [{"cita": "…", "explicacion": "…"}]
}
"""


# --- Datos -------------------------------------------------------------------

@dataclass
class Norma:
    rasgo: str          # ortografía | marcas de diálogo
    forma_obra: str     # lo que la obra usa siempre
    forma_rota: str     # lo que no usa nunca
    veces_obra: int
    veces_rota: int

    @property
    def evidencia(self) -> str:
        return (f"la obra usa «{self.forma_obra}» {self.veces_obra} veces "
                f"y «{self.forma_rota}» {self.veces_rota}")


@dataclass
class Perfil:
    normas: list[Norma]
    raices: Counter


@dataclass
class Aviso:
    clase: str          # norma | lexico
    rasgo: str
    cita: str
    gravedad: str
    evidencia: str
    palabra: str | None = None
    explicacion: str = ""


@dataclass
class Medida:
    avisos_norma: list[Aviso]
    candidatos: list[str]


@dataclass
class Entorno:
    antes: str = ""
    despues: str = ""


@dataclass
class Informe:
    avisos: list[Aviso]
    notas: list[dict]
    descartados: list[dict] = field(default_factory=list)
    llamadas: int = 0


# --- 1. Perfil de la obra ---------------------------------------------------

def raiz(palabra: str) -> str:
    return _lematizador.stemWord(palabra.lower())


def limpiar(texto: str) -> str:
    """Quita el aparato de la edición que no es voz. Si la obra numera sus
    párrafos (los versos de la Segalá), se quitan los números y las líneas
    sin número (títulos y pies de ilustración), salvo las que empiezan en
    minúscula: continúan un párrafo cortado por una ilustración. Una obra
    sin numeración se deja tal cual."""
    lineas = [l for l in texto.splitlines() if l.strip()]
    if not lineas or sum(bool(VERSO.match(l)) for l in lineas) < len(lineas) / 2:
        return texto
    return "\n".join(VERSO.sub("", l) for l in lineas
                     if VERSO.match(l) or l[:1].islower())


def contar_marcas(texto: str) -> dict[str, int]:
    return {m: len(re.findall(p, texto, re.M)) for m, p in MARCAS.items()}


def normas_de(texto: str) -> list[Norma]:
    palabras = Counter(p.lower() for p in PALABRA.findall(texto))
    normas = []
    for x, y in PARES:
        total = palabras[x] + palabras[y]
        if total < MINIMO_APARICIONES:
            continue
        for buena, rota in ((x, y), (y, x)):
            if palabras[buena] / total > PROPORCION_NORMA:
                normas.append(Norma("ortografía", buena, rota, palabras[buena], palabras[rota]))
    marcas = contar_marcas(texto)
    total = sum(marcas.values())
    if total >= MINIMO_APARICIONES:
        for buena, veces in marcas.items():
            if veces / total > PROPORCION_NORMA:
                normas += [Norma("marcas de diálogo", buena, rota, veces, marcas[rota])
                           for rota in marcas if rota != buena]
    return normas


def perfil_de_textos(textos: list[str]) -> Perfil:
    limpio = "\n".join(limpiar(t) for t in textos)
    return Perfil(normas=normas_de(limpio),
                  raices=Counter(raiz(p) for p in PALABRA.findall(limpio)))


def textos_de_obra(cur, obra: str) -> list[str]:
    cur.execute(
        """SELECT f.texto FROM fragmentos f JOIN obras o ON o.id = f.obra_id
           WHERE o.slug = %s ORDER BY f.orden_fragmento""",
        (obra,),
    )
    return [t for (t,) in cur.fetchall()]


def perfil_de_voz(cur, obra: str) -> Perfil:
    return perfil_de_textos(textos_de_obra(cur, obra))


# --- 2. Medir ---------------------------------------------------------------

def a_comienzo_de_frase(texto: str, inicio: int) -> bool:
    antes = texto[:inicio].rstrip()
    return not antes or antes[-1] in FIN_DE_FRASE


def normas_rotas(pasaje: str, normas: list[Norma]) -> list[Aviso]:
    avisos = []
    for n in normas:
        if n.rasgo == "ortografía":
            patron = rf"(?<!\w){re.escape(n.forma_rota)}(?!\w)"
            encontradas = re.finditer(patron, pasaje, re.IGNORECASE)
        else:
            encontradas = re.finditer(MARCAS[n.forma_rota], pasaje, re.M)
        for m in encontradas:
            avisos.append(Aviso(clase="norma", rasgo=n.rasgo, cita=m.group(0) or n.forma_rota,
                                gravedad="ruptura", evidencia=n.evidencia))
    return avisos


def candidatos_lexicos(pasaje: str, raices: Counter) -> list[str]:
    """Palabras del pasaje cuya raíz no está en la obra, sin repetir y en
    orden de aparición. Fuera: las formas de los pares de normas (tienen su
    aviso) y las mayúsculas a mitad de frase (nombres propios)."""
    formas_de_norma = {f for par in PARES for f in par}
    candidatos: list[str] = []
    for m in PALABRA.finditer(pasaje):
        p = m.group(0)
        if p[0].isupper() and not a_comienzo_de_frase(pasaje, m.start()):
            continue
        if p.lower() in formas_de_norma or raices[raiz(p)] > 0:
            continue
        if p.lower() not in (c.lower() for c in candidatos):
            candidatos.append(p)
    return candidatos


def medir(pasaje: str, perfil: Perfil, excluir: bool = False) -> Medida:
    """Con excluir, el pasaje es texto que ya está en la obra: sus palabras
    se descuentan del vocabulario (si no, ninguna sería nueva)."""
    raices = perfil.raices
    if excluir:
        raices = raices - Counter(raiz(p) for p in PALABRA.findall(limpiar(pasaje)))
    return Medida(avisos_norma=normas_rotas(pasaje, perfil.normas),
                  candidatos=candidatos_lexicos(limpiar(pasaje), raices))


# --- 3. Consultar al modelo -------------------------------------------------

def construir_prompt(medida: Medida, entorno: Entorno, pasaje: str) -> tuple[str, str]:
    """Prompt de sistema y mensaje del usuario. Función pura."""
    usuario = "\n\n".join([
        "TEXTO ANTES DEL PUNTO DE INSERCIÓN:\n---\n" + entorno.antes + "\n---",
        "TEXTO DESPUÉS DEL PUNTO DE INSERCIÓN:\n---\n" + entorno.despues + "\n---",
        "CANDIDATOS (su raíz no aparece en la obra): " + ", ".join(medida.candidatos),
        "PASAJE NUEVO:\n---\n" + pasaje + "\n---",
    ])
    return PROMPT_SISTEMA, usuario


def consultar_modelo(medida: Medida, entorno: Entorno, pasaje: str, cliente=None) -> dict:
    cliente = cliente or anthropic.Anthropic()
    sistema, usuario = construir_prompt(medida, entorno, pasaje)
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


# --- 4. Validar -------------------------------------------------------------

def contiene_palabra(texto: str, palabra: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(palabra)}(?!\w)", texto, re.IGNORECASE) is not None


def validar(respuesta: dict, medida: Medida, pasaje: str) -> Informe:
    """Garantías que no dependen del modelo (docs/agente_voz.md, «Validar»).
    Los avisos de norma vienen de medir, no del modelo. Función pura."""
    descartados: list[dict] = []
    formas_de_norma = {f for par in PARES for f in par}
    candidatos = {c.lower() for c in medida.candidatos}
    pasaje_normalizado = normalizar(pasaje)
    por_palabra: dict[str, Aviso] = {}

    for aviso in respuesta.get("avisos") or []:
        if not isinstance(aviso, dict):
            descartados.append({"motivo": "aviso mal formado", "aviso": aviso})
            continue
        cita, palabra = aviso.get("cita"), aviso.get("palabra")
        if not isinstance(cita, str) or not cita.strip() \
                or normalizar(cita) not in pasaje_normalizado:
            descartados.append({"motivo": "cita no literal", "aviso": aviso})
            continue
        if not isinstance(palabra, str) or palabra.lower() in formas_de_norma:
            descartados.append({"motivo": "forma de norma", "aviso": aviso})
            continue
        if palabra.lower() not in candidatos or not contiene_palabra(cita, palabra):
            descartados.append({"motivo": "palabra fuera de los candidatos", "aviso": aviso})
            continue
        if aviso.get("rasgo") not in RASGOS or aviso.get("gravedad") not in GRAVEDADES:
            descartados.append({"motivo": "valor no permitido", "aviso": aviso})
            continue
        nuevo = Aviso(clase="lexico", rasgo=aviso["rasgo"], cita=cita, gravedad=aviso["gravedad"],
                      evidencia=f"la raíz de «{palabra}» no aparece en la obra",
                      palabra=palabra, explicacion=str(aviso.get("explicacion", "")))
        anterior = por_palabra.get(palabra.lower())
        if anterior is None:
            por_palabra[palabra.lower()] = nuevo
            continue
        # Dos avisos sobre la misma palabra: se queda el de mayor gravedad.
        if GRAVEDADES.index(nuevo.gravedad) < GRAVEDADES.index(anterior.gravedad):
            por_palabra[palabra.lower()] = nuevo
        descartados.append({"motivo": "repetido", "aviso": aviso})

    notas = [{"cita": str(n.get("cita", "")), "explicacion": str(n.get("explicacion", ""))}
             for n in respuesta.get("notas") or [] if isinstance(n, dict)]
    return Informe(avisos=medida.avisos_norma + list(por_palabra.values()),
                   notas=notas, descartados=descartados)


# --- Todo junto -------------------------------------------------------------

def evaluar_en(perfil: Perfil, texto: str, pasaje: str, char: int, cliente=None) -> Informe:
    """Evalúa el pasaje en el carácter `char` del texto del fragmento.
    Sin candidatos léxicos no se llama al modelo."""
    if not 0 <= char <= len(texto):
        raise ValueError(f"El fragmento tiene {len(texto)} caracteres; char={char}.")
    medida = medir(pasaje, perfil, excluir=texto[char:char + len(pasaje)] == pasaje)
    if not medida.candidatos:
        return validar({}, medida, pasaje)
    entorno = Entorno(antes=texto[max(0, char - CARACTERES_ENTORNO):char],
                      despues=texto[char:char + CARACTERES_ENTORNO])
    informe = validar(consultar_modelo(medida, entorno, pasaje, cliente), medida, pasaje)
    informe.llamadas = 1
    return informe


def evaluar(cur, obra: str, pasaje: str, orden: int, char: int, cliente=None,
            perfil: Perfil | None = None) -> Informe:
    perfil = perfil or perfil_de_voz(cur, obra)
    return evaluar_en(perfil, texto_fragmento(cur, obra, orden), pasaje, char, cliente)


# --- Línea de comandos ------------------------------------------------------

def imprimir(informe: Informe) -> None:
    if not informe.avisos:
        print("Sin avisos.")
    for a in informe.avisos:
        print(f"AVISO ({a.gravedad}, {a.clase}: {a.rasgo}) «{a.cita}»")
        print(f"  {a.evidencia}")
        if a.explicacion:
            print(f"  {a.explicacion}")
    for n in informe.notas:
        print(f"NOTA «{n['cita']}» — {n['explicacion']}")
    print(f"\n{len(informe.descartados)} avisos descartados por la validación; "
          f"{informe.llamadas} llamadas al modelo.")


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
    with psycopg2.connect(args.dsn) as con, con.cursor() as cur:
        informe = evaluar(cur, args.obra, pasaje, args.orden, args.char)
    if args.json:
        print(json.dumps(asdict(informe), ensure_ascii=False, indent=2))
    else:
        imprimir(informe)


if __name__ == "__main__":
    main()
