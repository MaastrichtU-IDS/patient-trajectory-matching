# Packaging

Status: implemented, not published. The engine builds as an installable wheel in place; no code moved.

## Build and install

```sh
python -m pip install -r patterns/requirements.lock.txt   # the tested dependency set
python -m pip wheel --no-deps -w dist .
python -m pip install --no-deps dist/patient_trajectory_matching-0.26.0-py3-none-any.whl
```

`pyproject.toml` uses hatchling (pinned in `build-system.requires`), requires Python 3.10 or later, and depends on `rdflib`, `pyshacl` and `jsonschema` as lower bounds equal to the lock file. The lock files remain the tested set. Extras: `semantic` (`rustdl`, what `requirements-semantic.lock.txt` adds) and `store` (`duckdb`). There are no console scripts; run modules with `python -m patterns.<module>`.

## What the wheel contains

- `patterns/`, without `test_*.py`, `verify_*.py` and `__pycache__`, plus the three requirements files;
- `reference_oracle.py` as a top-level module, standard library only;
- the contracts the engine reads, as package data under `patterns/_contracts/`: `schemas/*.json`, `ontology/*.ttl`, `ontology/vendor/`, `ontology/sulo-pin.json` and `ontology/toy-taxonomy.json`.

The exact list is committed in `.github/scripts/expected-wheel-files.json`, and CI fails if a contract goes missing or a test leaks in. Nothing from `examples/`, `data/`, `verification/`, `docs/` or `ontology/legacy-2.3` is shipped.

## The resource locator

Engine modules build paths from `pro_solid.ROOT`. `patterns/_resources.py` defines it:

- **In a checkout** (`../schemas` and `../ontology` exist next to the package) it is the same `Path` as before, `Path(__file__).resolve().parents[1]`, so behaviour is identical.
- **Installed**, it is a small read-only path-like object. `ROOT / 'patterns/...'` maps to the package directory, `schemas/...` and `ontology/...` to `patterns/_contracts/...`, and any other first part raises `ValueError('REPOSITORY_RESOURCE_UNAVAILABLE:<path>')`. `str(ROOT)` and `os.fspath(ROOT)` give the directory containing the package.

Examples, data pins and reports are repository resources and are not available when installed. Nothing writes into site-packages. Only the `ROOT` definition in `pro_solid.py` changed. Other modules keep their own `ROOT` (`patient_similarity`, `mimic_inputevents`, `mimic_matcher_handoff`); those only compute paths, and they resolve to the site-packages directory when installed.

Known limit: `patterns.temporal_interface` computes `EXAMPLES = ROOT / 'examples/temporal-interface'` at import, so it cannot be imported from an installed wheel. It is only used by its own runner and test, which need the examples anyway.

## Checks

`patterns/test_packaging.py` tests the locator against a temporary site-packages-like directory. The `package` workflow builds the wheel, installs it with `--no-deps` into a fresh venv holding only the lock file, and from outside the checkout (`python -I`) checks that `patterns` imports from site-packages, that the file list matches, and that the oracle, the trajectory-graph example, the bounded-interval context identifier and a PRO/SOLID graph with SHACL validation give the same results as the checkout.

## Deliberately not done (D1)

No publishing, no console scripts, no move to `src/` (it would break every report binding by path), and no rename of the generic import name `patterns`, which waits for stage 3. Direct reads of `ROOT / 'schemas/...'` in about 60 modules are not rewritten (D2).
