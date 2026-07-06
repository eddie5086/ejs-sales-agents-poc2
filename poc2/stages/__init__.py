"""Product stage strategies. Importing this package populates the registry —
the engine then resolves every (kind, strategy) pair declared in pipeline
YAMLs."""
from poc2.stages import (  # noqa: F401
    browser_fetch, conditions, crm_enrich, enrich, fetch, generate,
    persist, prioritize, reconcile, research, summary, validate, verify,
)
