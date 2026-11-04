"""Runtime services the research workspace builds on.

These modules used to live in demo/, so the product imported from its own demonstration.
They are the parts the workspace and the evaluation tools need at runtime:

- cohort: the guided synthetic cohort, run through the reference oracle
- pressure, pressure_cache: the reviewed blood-pressure query service and its result cache
- mapped_pressure: the same service over reviewed measurement mappings

The HTTP servers and pages that present them remain in demos/presentation/.
"""
