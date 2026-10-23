import pytest

from jobradar.config import load_config


@pytest.fixture
def config():
    return load_config()
