# ADR 0001: Run PySpark pipelines inside a Docker container

**Status:** accepted (2026-11-10)

## Context
PySpark needs a Java runtime. Spark 3.5 supports Java 8/11/17, but the development Mac has
Java 23. The project owner wants to write only Python and has limited disk space.
Pipelines must also run on Databricks Free Edition, which provides its own Spark.

## Decision
Build a small image `txlens-spark:local` from `python:3.12-slim-bookworm` with
`openjdk-17-jre-headless`, PySpark 3.5 and delta-spark 3.3 (locked versions from `uv.lock`).
Delta jars are downloaded at build time. The repo is mounted into the container, so code
edits do not need a rebuild. `make pipeline` and `make test-pipelines` run inside it.
PySpark is an optional extra (`txlens-pipelines[spark]`), so the host virtualenv stays small.
CI installs the extra and uses the runner's Java 17 directly.

## Alternatives
- Install Java 17 on the Mac: works, but more host setup and version juggling.
- Official `apache/spark` image: larger and ships an older Python than 3.12.
- Use only Databricks: slow feedback loop, needs internet, harder to test in CI.

## Consequences
- About 1 GB of disk for the image; Docker Desktop must be running for pipeline work.
- Host mypy cannot see PySpark types (ignored there); CI type-checks with PySpark installed.
