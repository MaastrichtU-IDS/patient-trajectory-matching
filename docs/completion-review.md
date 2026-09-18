# Integrated recorded-workflow review

This review covers the recorded-pattern, explicit-feature, durable-job and owner-access additions. It is a technical review of the implemented scope, not a clinical acceptance or security certification.

## Checks and corrections

- Measurements remain points; recorded treatment segments remain intervals. The compiler admits only the fixed reviewed items/units and windows. End-relative follow-up requires proof that every retained anchor remains inside the admitted temporal envelope. Optional missing follow-up does not become an exclusion.
- Similarity uses only the declared pre-index window and selected features. Both window endpoints matter when a pattern excludes measurements near treatment start. Rankings use exact rational arithmetic; displayed rounding cannot create a tie. Missing requested features remain explicit rather than imputed.
- Pattern revisions preserve original evidence and the admitted roster. The portable canonical context refers to the original base query, allowing the final pattern to replay independently of intermediate job identifiers. Immediate operational parent job IDs remain available in job history.
- Completed snapshots survive restart without reopening changed sources. Editing or resuming invokes admission checks. A single-instance file lock prevents a second process from recovering a live process's jobs. Pattern inspection and persistence use consistent lock order.
- Owner access protects the interface, static assets and APIs before request-body processing. Health probes remain public. Credentials are loaded from a private regular file, retained as a digest, and checked with constant-time digest comparison. Audit entries contain fixed metadata fields, never request bodies, observations or authorization headers.

The independent review found and prompted corrections to HTTP audit status labels, a lock-order inversion, and the ignored upper endpoint of narrowed feature windows. It also checked second-revision replay rather than assuming one-revision coverage was sufficient.

## Executable acceptance

Run with the repository's installed dependencies:

```sh
python -m unittest app.test_completed_workflow_http
python -m unittest app.test_feature_profiles app.test_recorded_pattern
```

The HTTP acceptance uses the actual reviewed engine and exercises:

1. Missing and incorrect credentials, then successful protected interface/API access.
2. A completed query followed by two immutable typed-pattern revisions.
3. Explicit weighted comparison and exact contribution reconciliation.
4. Export and full recomputation, including the final pattern and feature profile.
5. Rejection of at-index baselines and follow-up outside the reviewed envelope.
6. Restart with identical historical jobs, comparisons and exports.
7. A retained interrupted intent, explicit HTTP resume, completion under the same identifier, and rejection of re-resuming a completed job.
8. Audit field whitelisting and credential non-disclosure.

## Scope still requiring external evidence

The four feature types derive from the same reviewed pressure measurement stream; they do not establish a clinically validated multi-variable similarity model. Independent SQL/Python agreement on admitted public-demo records establishes computational consistency, not clinical correctness of the selected source boundary. Clinical relevance judgments, full credentialed MIMIC-IV validation and representative production-scale performance require their own evidence. A single owner and private local SQLite database do not provide multi-user authorization or institutional deployment approval.

No full visual browser rehearsal is asserted by this review. HTTP tests and the JavaScript interface harness do not replace a rendered-browser usability and accessibility review.
