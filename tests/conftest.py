import pytest
from alometro.sintetico import generar

SAL = bytes.fromhex("8f" * 32)


@pytest.fixture(scope="session")
def convs_antes():
    return generar(150, "antes", semilla=7)


@pytest.fixture(scope="session")
def convs_despues():
    return generar(150, "despues", semilla=7)
