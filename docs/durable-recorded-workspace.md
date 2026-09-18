# Durable recorded workspace

The recorded journey can optionally keep completed jobs and accepted job intents
in a private SQLite database. Without durable storage, its existing three-job
in-memory service history remains unchanged. The database is local application
state, not a portable source package or a clinical data release.

## Configuration and access

Start the application with a local state directory using `--state-dir PATH`.
Use the server's `--auth-file PATH` option when sharing access: its fixed `owner`
account authenticates against an operator-created credential file. This is a
single-owner instance, not a multi-user authorization system. The optional durable
and owner-access modes require a POSIX/Linux runtime with Unix file permissions
and `fcntl` locking. See the server
command help for the credential-file format. Basic authentication requires TLS
at the reverse proxy whenever access crosses a trusted local boundary.

Create a private credential file without printing the secret to a terminal:

```sh
python - <<'PY_AUTH'
import os
import secrets
from pathlib import Path
path = Path.home() / '.ptm-owner'
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as output:
    output.write('owner:' + secrets.token_urlsafe(48) + '\n')
PY_AUTH
python -m app.server --host 127.0.0.1 --port 8080 \
  --state-dir "$HOME/.ptm-state" --auth-file "$HOME/.ptm-owner"
```

Open `http://127.0.0.1:8080/journey`. The browser asks for username `owner` and the
secret after `owner:` in that file; retrieve it locally with your password manager
or editor. The file must belong to the server process user, be a regular file
with no group/other permissions, and contain `owner:<secret>` with an optional
trailing newline. Secrets require 32–256 printable non-space ASCII characters.
Symlinks are refused. Credentials are loaded at startup; restart after rotation.

The server accepts authenticated mode only on a loopback listener. All application
pages, static assets and APIs require the owner credential; `/healthz` and `/readyz`
remain public minimal probes. Authentication is optional for authored local demos.
It grants one owner access to the whole instance, without patient-level roles.
The deployment chart does not configure this local mode or a TLS proxy for it.

The store creates `recorded-jobs.sqlite3` with mode 0600, and newly created state
directories with mode 0700. Existing state directories must already be owner-only and owned by the server
user; symlinks, unsafe permissions and nonregular database files are refused. Keep this directory outside the repository and outside
publicly served paths. No credentials are stored in the database. The database
contains retained source evidence and must receive the same local access controls
as its input data.

A lifetime operating-system file lock permits one active instance per database.
A second instance fails rather than recovering jobs belonging to the first.
The initial database schema is version 1; unknown versions and different
configuration namespaces are refused. Namespaces associate each profile with
its configured source directory, mapping directory, source mode, and configured
review-context identifier. Changing that namespace requires a separate state
directory; there is no implicit migration or merger of source populations.

## Completion, recovery and resume

An accepted intent is queued before execution. Each execution receives a service
job identifier, while the application job identifier remains stable across
restart and explicit resume. States are `queued`, `running`, `completed`, `failed`
and `interrupted`. New execution still passes the existing source, review,
configuration and implementation checks.

A callback in the service's worker queue saves completion without relying on a
browser polling the job. Job status, complete evidence and the audit event commit
in one transaction. Completed evidence must pass the same anchor-completeness,
source/query context and independent SQL-agreement checks used for export. A
partially verified or failed computation never becomes a completed snapshot.
A retention failure marks the job failed; it does not publish partial evidence.

At exclusive startup, previously queued or running intents become interrupted.
The history view exposes them for explicit resume. No incomplete result is
reconstructed, and no job silently restarts. Resume reruns source admission and
the original request under the same application identifier; it does not continue
from an arbitrary intermediate engine state. For an initial query, this is a new
admitted computation over the currently configured records, not a promise that
source files are unchanged since interruption. Its completed source context
records what was actually admitted. A pattern revision additionally requires its
retained parent's exact source context and fails if that context can no longer
be re-established.

Completed historical jobs can be inspected, compared and exported after restart
without reopening changed source files. They describe the retained source state.
Editing an archived pattern reopens and admits the current source when its live
session is absent and requires its source-context identifier to match the archived
job. Export replay remains stricter: it re-executes using current admitted local
sources and compares the declared source, query and implementation provenance.
Historical restoration alone does not establish successful replay.

## Retention and integrity

Each canonical serialized job record, including its request and evidence, is
limited to 8 MiB. Defaults retain at most 100 jobs and 64 MiB of job payloads,
independent of the service's three-job memory cache. Oldest terminal jobs are
evicted first, including interrupted intents; expired intents cannot resume.
Active jobs are never evicted. If active jobs occupy the capacity, a new write
fails atomically. Limits are constructor settings in `RecordedStore`.

SQLite uses full synchronous transactions, rollback journals, and full auto-vacuum.
The byte cap covers serialized job payloads, not SQLite page/index overhead or
transient rollback-journal space. Reserve additional disk space for that overhead
and operational backups; this is not an operating-system disk quota.

Every read verifies the stored SHA-256 digest and metadata association. Database
startup also performs SQLite's quick integrity check. Corrupted records fail
closed. Digests detect accidental corruption; they do not authenticate evidence
against someone who can rewrite the database and its hashes.

Audit records contain only a monotonic sequence, UTC timestamp, fixed actor,
short action identifier, application job identifier when applicable, and status.
They do not contain requests, measurements, raw exception text, URLs or
credentials. Application code appends records rather than editing them; explicit
retention removes the oldest entries after 10,000 records by default. This local,
bounded audit history is neither tamper-proof nor a clinical compliance system.

## Backup and operation checks

For an ordinary file copy, shut down the server cleanly first, then copy the
SQLite database to access-controlled storage. A live backup should use SQLite's
backup API rather than copying a database during a write. The `.lock` file is
not evidence and need not be copied. Restore with the same application schema
and configuration namespace before reopening the service. Keep enough filesystem
space for the database, transaction journal and backup.

Run the durability checks with:

```sh
python -m unittest app.test_recorded_store
```

They cover restart restoration without browser polling, explicit recovery and
resume, configuration/schema refusal, exclusive ownership, payload integrity,
transaction rollback, complete-evidence checks, private file permissions, and
bounded retention. They use authored test inputs only.
