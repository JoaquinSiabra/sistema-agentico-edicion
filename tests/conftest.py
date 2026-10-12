"""Configuración compartida de la suite.

Por defecto solo corre la suite rápida (segundos, sin base de datos ni API).
Dos niveles más, que hay que pedir expresamente:
  --db   pruebas con Postgres + pgvector y el modelo de embeddings
         (en este proyecto, desde WSL);
  --api  pruebas que llaman de verdad a Claude: cuestan dinero y su
         resultado puede variar entre ejecuciones.
"""

from types import SimpleNamespace

import pytest

NIVELES = {
    "db": "necesita Postgres + pgvector (activar con --db)",
    "api": "llama de verdad a la API de Claude, con coste (activar con --api)",
}


def pytest_addoption(parser):
    parser.addoption("--db", action="store_true",
                     help="ejecutar también las pruebas con base de datos (nivel 2)")
    parser.addoption("--api", action="store_true",
                     help="ejecutar también las pruebas contra la API real (nivel 3, con coste)")


def pytest_configure(config):
    for marca, descripcion in NIVELES.items():
        config.addinivalue_line("markers", f"{marca}: {descripcion}")


def pytest_collection_modifyitems(config, items):
    for marca in NIVELES:
        if config.getoption(f"--{marca}"):
            continue
        saltar = pytest.mark.skip(reason=f"usar --{marca}")
        for item in items:
            if marca in item.keywords:
                item.add_marker(saltar)


# --- API simulada (nivel 3) -------------------------------------------------

class ClienteFalso:
    """Devuelve las respuestas indicadas, una por llamada, y guarda las peticiones."""

    def __init__(self, respuestas: list[str]):
        self.respuestas = list(respuestas)
        self.peticiones: list[dict] = []
        self.messages = self

    def __call__(self):           # anthropic.Anthropic() -> este mismo objeto
        return self

    def create(self, **peticion):
        self.peticiones.append(peticion)
        return SimpleNamespace(
            content=[SimpleNamespace(text=self.respuestas.pop(0))],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )


@pytest.fixture
def api(monkeypatch):
    """api(respuestas) instala un ClienteFalso en los tres scripts."""
    import calibrar_glosario
    import calibrar_taxonomia
    import extraer_canon

    def instalar(respuestas):
        cliente = ClienteFalso(respuestas)
        for modulo in (calibrar_glosario, calibrar_taxonomia, extraer_canon):
            monkeypatch.setattr(modulo.anthropic, "Anthropic", cliente)
        return cliente
    return instalar
