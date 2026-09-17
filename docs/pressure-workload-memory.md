# Memory measurement for configured pressure workloads

The configured workload verifier establishes agreement between prepared, cached and fresh execution. Its serialized cache sizes do not measure process memory. `demo/profile_pressure_workload.py` adds a separate Linux parent process that samples the resident memory of the fixed verifier and its visible descendants through `/proc`.

Run it from the repository root, with the same Python environment required by the [configured workload verifier](configured-pressure-workload.md):

```bash
python demo/profile_pressure_workload.py \
  --config examples/configured-pressure-service/config.json \
  --workload examples/configured-pressure-service/workload.json \
  --repetitions 3 \
  --interval 0.02 \
  --timeout 3600 \
  --output verification/pressure-workload-memory-report.json
```

The profiler launches only `demo/benchmark_configured_pressure.py`, using the current Python executable and a new process session. It provides no arbitrary command option. The existing workload limits remain: one to eight query variants and one to ten repetitions. Sampling intervals must be 5–1,000 ms; the wall-time limit is 1–86,400 seconds. The deadline is checked between scans, so a scan or operating-system scheduling delay can extend it slightly.

## What is measured

Each scan reads Linux `/proc/<pid>/stat`, identifies the runner and descendants by parent relationships, and also includes processes retaining the runner's session after reparenting. RSS pages are multiplied by the system page size and added across the selected processes. The report stores the maximum of these sampled aggregates. Process identities include their start time internally so reused process numbers do not collapse distinct observations; process numbers are not exported.

This measures the whole verification workload: Python startup after subprocess creation, cold admission, HTTP service jobs, repeated queries, fresh references, and visible worker subprocesses. The parent profiler, input preflight, and final report writing are excluded. `elapsed_seconds` spans monitoring immediately after subprocess creation through final child cleanup. It is not a query latency or a cold-preparation latency. Use the separate workload verifier's report for those timings.

The maximum sampled aggregate RSS is **not an exact simultaneous process-tree peak**. `/proc` reads are sequential; allocation peaks and short-lived subprocesses between scans can be missed. Sequential samples can also combine observations from slightly different times, so they are not a strict lower bound on the true simultaneous peak. RSS counts shared pages in each process and therefore is not unique physical memory or proportional set size. Detached descendants that also become reparented can escape discovery. No historical per-process high-water marks are summed.

The report makes these limits explicit and always sets `coverage_complete` to false. It includes the requested interval, actual maximum gap and scan duration, number of samples with a visible root or resident memory, observed process counts, and unreadable/exited process-record count. That last counter covers all numeric `/proc` entries inspected, including unrelated processes.

## Verification and failure behavior

A `MEASURED` result requires a zero child exit code, a `VERIFIED` child report with the expected workload context, agreement in every reported trial, unchanged configured inputs and implementation, and actual resident-memory observations including the runner. Verification counts, source-table hashes, the child report hash, and implementation hashes accompany the measurement. Raw child stdout/stderr, source paths, patient rows, and identifiers are not copied into the report. The intermediate child report is kept in a private temporary directory and removed afterward.

Unsupported operating systems, unavailable `/proc`, timeout, failed verification, missing samples, and execution errors produce `FAILED` reports with fixed aggregate failure codes. Failure details from child records or stderr are discarded. Invalid initial arguments or protected output paths are rejected before launching a child or writing a report. The existing config, workload, request, review, implementation artifacts, and source/mapping directories are protected against output replacement. The child process session is killed and the root child is reaped on timeout or sampling failure; same-session descendants are also killed after normal root exit.

## Authored example

The [committed example report](../verification/pressure-workload-memory-report.json) records six verified trials and 36 matching HTTP anchor inspections for the five-stay authored fixture. On the development machine, the observed aggregate reached **81,317,888 bytes (77.55 MiB)** across 543 scans over 11.39 seconds. The requested interval was 20 ms; the largest observed gap was 27.19 ms. At most two processes were visible together, with 16 distinct process instances observed over the run.

These values demonstrate an executable measurement method and are machine-specific observations. The report explicitly leaves `representative_clinical_scale_established` and `clinical_mapping_verified` false. Representative clinical-scale evaluation still requires an appropriately reviewed dataset and workload; this authored fixture does not establish capacity or a production memory limit.

Targeted checks:

```bash
python -m unittest discover -s demo -p 'test_pressure_workload_profile.py' -v
```
