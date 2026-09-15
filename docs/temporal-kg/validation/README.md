# Reproduce the v2 report's 18 OWL checks

This package supplies the original artifacts requested in [issue #6](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/issues/6). It corresponds to §11 of [Temporal Knowledge Graph: Revised formal definition, v2 working specification, 15 September 2026](../Temporal_Knowledge_Graph_Formal_Definition_v2.pdf).

## Report and original-run provenance

The PDF matches the authoring-workspace PDF byte-for-byte. It entered this repository in [a957d91028c9db06c4c29fb2a57a078897bde2b4](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/commit/a957d91028c9db06c4c29fb2a57a078897bde2b4). Both ontology files, the Java checker and the retained original outputs are copied unchanged from that workspace. No ontology axioms or original checks were corrected for this handoff.

| Artifact | Role |
|---|---|
| [temporal-core.ofn](../ontology/temporal-core.ofn) | Original standalone OWL core |
| [example.ofn](../ontology/example.ofn) | Original self-contained core plus administration/collection fixture |
| [ValidateTemporal.java](ValidateTemporal.java) | Original checker, unchanged |
| [Original results](results/original-2026-09-15/) | Retained raw checker/profile outputs and a separately reconstructed per-check table |
| [Rerun results](results/rerun-2026-09-15/) | Fresh execution, command/exit-code metadata, stdout/stderr and per-check table |
| [checks.json](checks.json) | Ordered inventory: input, expression, expected Boolean and checker line |
| [provenance.json](provenance.json) | Report correspondence, original-file digests and pinned tool versions |
| [run.py](run.py) | New reproduction wrapper around the unchanged Java checker |

Both `.ofn` files have **no imports**. The example includes the core axioms itself. No import catalog, network access or other ontology is required to load them. Their `example.org` ontology IRIs are identifiers, not dependencies to download. Each mutation fixture is specified directly in the checker, with its input summarized below.

The original run occurred on 15 September 2026 (Asia/Riyadh). Its [checker transcript](results/original-2026-09-15/reasoner-checks.txt) contains 18 `PASS` lines and a terminal summary. The original logging did not retain separate runtime/compile stderr, timestamped command logs or raw Boolean fields. We have not reconstructed those as purported original logs. The original [per-check TSV](results/original-2026-09-15/checks.tsv) was added for this handoff: each actual Boolean follows from the successful assertion in the unchanged source, linked to its original transcript line. The rerun wrapper uses the same interpretation of the transcript.

The original [document-checks.txt](results/original-2026-09-15/document-checks.txt) records PDF checks; those are separate from the 18 OWL checks. The standalone ROBOT profile command repeats core-profile check C01 and is not a nineteenth check.

## Tools and reproduction

The original and committed rerun used:

| Component | Version |
|---|---|
| ROBOT | 1.9.10 release JAR |
| Bundled OWLAPI | 4.5.29 |
| Bundled HermiT | 1.4.5.456 |
| Java / javac | OpenJDK 21.0.12.1, Debian build `21.0.12.1+1-1-deb13u1-Debian` |
| New wrapper | Python 3.10+ standard library; committed rerun used 3.13.5 |

The OWLAPI/HermiT versions were recovered from Maven metadata in the pinned original JAR. The [upstream ROBOT release](https://github.com/ontodev/robot/releases/tag/v1.9.10) distributes the same JAR digest recorded in `provenance.json`. The JAR is downloaded separately; it is not committed here.

From the repository root, with a JDK installed:

```sh
mkdir -p docs/temporal-kg/validation/.cache
curl --fail --location \
  https://github.com/ontodev/robot/releases/download/v1.9.10/robot.jar \
  --output docs/temporal-kg/validation/.cache/robot.jar
python3 docs/temporal-kg/validation/run.py \
  --robot-jar docs/temporal-kg/validation/.cache/robot.jar
```

Alternatively pass an existing v1.9.10 JAR to `--robot-jar`. The wrapper verifies its digest and the archived input/report/output digests before execution. After the JAR is available, the run is offline. It copies the ontologies into a temporary `formal-definition-v2/` directory so the unchanged checker's original relative-path arguments and transcript are preserved.

Expected: `PASS: 18/18 checks`, exit 0. New outputs go to the gitignored `results/local-run/`. Supply `--output /path/to/a/new-directory` for another run. Existing output directories, including original evidence, are refused. Failures return nonzero; inspect raw stdout/stderr and `checks.tsv`. `NOT_OBSERVED` means the original fail-fast checker did not emit that check's result, and is never counted as a pass.

### Underlying commands

The wrapper runs these commands, with `ROBOT_JAR` resolved to the pinned JAR, `CHECKER` to the original Java source, and `CLASSES` and `OUTPUT` to newly created directories. These variables are explanatory placeholders; the wrapper records the exact argument arrays and working directory for every actual command in `run.json`.

```sh
java -version
javac -version
java -jar "$ROBOT_JAR" --version
java -jar "$ROBOT_JAR" validate-profile \
  --input formal-definition-v2/temporal-core.ofn \
  --profile DL --output "$OUTPUT/owl2dl-profile.txt"
javac -cp "$ROBOT_JAR" -d "$CLASSES" "$CHECKER"
java -cp "$CLASSES:$ROBOT_JAR" ValidateTemporal \
  formal-definition-v2/temporal-core.ofn formal-definition-v2/example.ofn
```

The first three commands capture version information for the new run. The last three reproduce the original validation operations. `run.py` handles the classpath separator for the host platform.

## Check inventory and results

Expected/actual values below are the Boolean result of the expression in `checks.json`, **not** a blanket “true means pass”: intentional inconsistency and absent-entailment checks expect `false`. Both the original transcript and the committed rerun establish the listed actual values. Every check passed in both runs. The input column identifies the base ontology; temporary assertions and queries are listed in `checks.json` and the linked source line.

| ID | Check | Input | Expected | Actual | Outcome | Source |
|---|---|---|---|---|---|---|
| C01 | Core in OWL 2 DL | `temporal-core.ofn` | `true` | `true` | PASS | [L29](ValidateTemporal.java#L29) |
| C02 | Example in OWL 2 DL | `example.ofn` | `true` | `true` | PASS | [L29](ValidateTemporal.java#L29) |
| C03 | example consistent | `example.ofn` | `true` | `true` | PASS | [L31](ValidateTemporal.java#L31) |
| C04 | all named classes satisfiable | `example.ofn` | `true` | `true` | PASS | [L32](ValidateTemporal.java#L32) |
| C05 | antibiotic administration inferred | `example.ofn` | `true` | `true` | PASS | [L33](ValidateTemporal.java#L33) |
| C06 | PRO chain infers bearer participation | `example.ofn` | `true` | `true` | PASS | [L34](ValidateTemporal.java#L34) |
| C07 | interval occurrence inferred as ExtendedProcess | `example.ofn` | `true` | `true` | PASS | [L35](ValidateTemporal.java#L35) |
| C08 | point and interval on same individual inconsistent | `temporal-core.ofn` | `false` | `false` | PASS | [L37](ValidateTemporal.java#L37) |
| C09 | identical beginning and end inconsistent | `temporal-core.ofn` | `false` | `false` | PASS | [L38](ValidateTemporal.java#L38) |
| C10 | interval with unnamed endpoints consistent | `temporal-core.ofn` | `true` | `true` | PASS | [L39](ValidateTemporal.java#L39) |
| C11 | process with unknown extent consistent | `temporal-core.ofn` | `true` | `true` | PASS | [L40](ValidateTemporal.java#L40) |
| C12 | point occurrence consistent | `temporal-core.ofn` | `true` | `true` | PASS | [L41](ValidateTemporal.java#L41) |
| C13 | point and interval exact extents on one process inconsistent | `temporal-core.ofn` | `false` | `false` | PASS | [L42](ValidateTemporal.java#L42) |
| C14 | two-way pointBefore inconsistent | `temporal-core.ofn` | `false` | `false` | PASS | [L43](ValidateTemporal.java#L43) |
| C15 | functional endpoint infers equality | `temporal-core.ofn` | `true` | `true` | PASS | [L46](ValidateTemporal.java#L46) |
| C16 | different functional endpoint fillers inconsistent | `temporal-core.ofn` | `false` | `false` | PASS | [L47](ValidateTemporal.java#L47) |
| C17 | pointBefore closure not entailed by core | `temporal-core.ofn` | `false` | `false` | PASS | [L49](ValidateTemporal.java#L49) |
| C18 | reversed interval endpoint order awaits external validation | `temporal-core.ofn` | `true` | `true` | PASS | [L50](ValidateTemporal.java#L50) |

C01–C02 are profile checks. C03–C18 are consistency, class-satisfiability or entailment checks performed with HermiT. The original checker exits on its first failed assertion, so both a successful exit and all 18 identified lines are required for a complete pass.

## Scope and interpretation

This is evidence for the report's **standalone draft ontology**, using local properties and the local PRO pattern. It establishes neither full SULO/OWL-Time import alignment nor correctness of a temporal query engine. It is a separate optional Java validation tool, and leaves the project's Rust/Python implementation direction and current application profiles unchanged.

The limits are exercised directly: C10 accepts an interval with unnamed endpoints, C15 shows functional endpoints inferring equality, C17 shows that the simple `pointBefore` property has no OWL transitive closure, and C18 accepts reversed interval endpoints in OWL pending external temporal validation. The original v2 report flags those operational boundaries.

The committed rerun reproduces the original checker transcript and profile output byte-for-byte. Its new wrapper, inventories, hashes and execution metadata are additions made for issue #6; the original run files remain separately identified.
