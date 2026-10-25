import copy

import pytest

from jobradar.config import load_config


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def authorized_config():
    """Every source switched on, so tests cover each integration even when config.yaml has some off."""
    c = copy.deepcopy(load_config())
    c["sources"]["jobspy"].update(enabled=True, permitted=True, sites=["naukri", "linkedin", "indeed", "glassdoor", "google"])
    for name in ("adzuna", "jooble", "remotive", "arbeitnow"):  # off in config.yaml; tests still cover them
        c["sources"][name]["enabled"] = True
    return c
