"""Source registry. Add a module here and a `sources:` entry in config.yaml."""

from . import adzuna, arbeitnow, ashby, greenhouse, jooble, lever, remotive, smartrecruiters

REGISTRY = {
    m.NAME: m
    for m in (greenhouse, lever, ashby, smartrecruiters, adzuna, jooble, remotive, arbeitnow)
}
