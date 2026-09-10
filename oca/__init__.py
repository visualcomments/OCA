"""OCA — Open Course Advisor.

An auditing and improvement tool for open educational course repositories,
modelled on OSA (Open-Source Advisor) but aimed at courses instead of
scientific software.

OCA answers two questions about a course repository:
  1. Is it usable?  — structure, licence, reproducibility, agent-readiness
  2. How to fix it? — generated artefacts and a prioritised plan

The deterministic half lives in :mod:`oca.scanner` and never depends on a
language model; the judgement half is driven by the skills in ``skills/``.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
