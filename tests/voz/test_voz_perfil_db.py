"""El perfil de la obra cargada en el almacén es el de sus textos.

El banco de Voz y edición toma el perfil de tests/fijos/textos/ para no
necesitar base de datos; esta prueba comprueba que perfil_de_voz, que lo
lee del almacén, da lo mismo con los Cantos I-IV cargados como obra aparte.
"""

import pytest

from almacen_prueba import FIJOS, FIJOS_III_IV, exports_de_prueba, obra_aparte
from banco_voz import perfil_del_banco
from voz import perfil_de_voz

pytestmark = pytest.mark.db

OBRA = "iliada_voz"


@pytest.fixture(scope="module")
def con(bd, modelo, tmp_path_factory):
    exports = exports_de_prueba(tmp_path_factory.mktemp("voz") / "exports", glosario=FIJOS_III_IV,
                                canon_de={1: FIJOS, 2: FIJOS, 3: FIJOS_III_IV, 4: FIJOS_III_IV})
    with obra_aparte(bd, modelo, OBRA, exports) as conexion:
        yield conexion


def test_el_perfil_del_almacen_es_el_de_los_textos(con):
    with con.cursor() as cur:
        assert perfil_de_voz(cur, OBRA) == perfil_del_banco()
