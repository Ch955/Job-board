"""Source registry. Add a module here and a `sources:` entry in config.yaml.

Order matters: when the same job appears in several sources, the earlier one
wins (a company's own careers page beats LinkedIn/Naukri beats aggregators).
"""

from . import adzuna, arbeitnow, ashby, greenhouse, jobspy_boards, jooble, lever, remotive, smartrecruiters

REGISTRY = {
    m.NAME: m
    for m in (greenhouse, lever, ashby, smartrecruiters, jobspy_boards, adzuna, jooble, remotive, arbeitnow)
}
