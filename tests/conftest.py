import copy

import pytest

from jobradar.config import load_config


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def authorized_config():
    """As if LinkedIn/Naukri/Indeed access were authorized: tests that the integration still works."""
    c = copy.deepcopy(load_config())
    c["sources"]["jobspy"].update(enabled=True, permitted=True)
    return c
