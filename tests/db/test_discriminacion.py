"""Calidad mínima de la búsqueda semántica sobre los Cantos I y II.

Prueba de regresión: fija el nivel que da hoy el troceado en pasajes, para
que un cambio de modelo, de tamaño de pasaje o de prefijo e5 que lo
empeore se note. No mide la calidad absoluta.

Para cada consulta, el mejor pasaje debe ser del canto correcto y algún
pasaje con la palabra clave del episodio debe salir entre los primeros.
La clave no siempre está en el mejor pasaje aunque este sea el episodio
correcto (el discurso de Iris a Héctor no repite su nombre).
"""

import pytest

from recuperar import buscar_pasajes

pytestmark = pytest.mark.db

# Peor caso medido el 2026-10-12: Iris, puesto 4. Si empeora, algo se ha roto.
PUESTO_MAXIMO_DE_LA_CLAVE = 5

CONSULTAS = [
    # (canto esperado, palabra clave del episodio, consulta)
    (1, "Crises", "El sacerdote Crises acude a las naves para rescatar a su hija"),
    (1, "cólera", "La cólera de Aquiles causó infinitos males a los aqueos"),
    (1, "Briseida", "Agamenón se lleva a Briseida de la tienda de Aquiles"),
    (1, "Vulcano", "Tetis suplica a Júpiter en el Olimpo y Vulcano escancia néctar a los dioses"),
    (2, "Sueño", "Júpiter envía un sueño engañoso a Agamenón mientras duerme"),
    (2, "Tersites", "Tersites insulta a Agamenón y Ulises lo golpea con el cetro"),
    (2, "beocios", "Catálogo de las naves: los beocios y sus caudillos"),
    (2, "Iris", "Iris avisa a los troyanos y Héctor reúne a los aliados"),
]


@pytest.mark.parametrize("canto, clave, consulta", CONSULTAS, ids=[c[1] for c in CONSULTAS])
def test_encuentra_el_episodio(cur, modelo, obra, canto, clave, consulta):
    filas = buscar_pasajes(cur, modelo, obra, consulta, hasta_fragmento=2, k=10)
    assert filas[0].orden_fragmento == canto
    puesto = next((i + 1 for i, p in enumerate(filas)
                   if p.orden_fragmento == canto and clave in p.texto), None)
    assert puesto is not None and puesto <= PUESTO_MAXIMO_DE_LA_CLAVE, (
        f"'{clave}' aparece en el puesto {puesto}")
