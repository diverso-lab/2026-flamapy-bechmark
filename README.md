# Flamapy Solver Benchmark

Benchmarks all solver backends available in the [Flamapy](https://flamapy.github.io/) framework — PySAT (six SAT backends), BDD, Z3, and the legacy Java FaMA tool — across 1,611 UVL feature models from [UVLHub](https://www.uvlhub.io/).

Pre-computed results and plots are included in `results/` and `plots/`.

## Solvers and operations

| Solver | Variants | Operations |
|--------|----------|------------|
| **PySAT** | glucose3, glucose4, minisat22, lingeling, maplesat, cadical153 | Satisfiable, CoreFeatures, DeadFeatures, FalseOptionalFeatures, ConfigurationsNumber |
| **BDD** | — | Satisfiable, CoreFeatures, DeadFeatures, FalseOptionalFeatures, ConfigurationsNumber, VariantFeatures, PureOptionalFeatures, UniqueFeatures, Variability, CommonalityFactor, Homogeneity, ProductDistribution, FeatureInclusionProbability, ConfigurationsWithNFeatures |
| **Z3** | — | Satisfiable, CoreFeatures, DeadFeatures, FalseOptionalFeatures, ConfigurationsNumber, AllFeatureBounds |
| **FaMA** | Choco, JaCoP, Sat4j | Valid, #Products, DetectErrors, Variability |

FaMA is a legacy Java tool. It is optional: the Docker image builds it automatically; the manual setup requires JDK 11+ and Maven 3.6+.

---

## Reproducing the benchmark

### Option A — Docker (recommended)

```bash
# Build the image (downloads and compiles FaMA — takes ~5 min the first time)
docker build -t flamapy-benchmark .

# Run the full benchmark; results land in ./output/
mkdir -p output
docker run --rm -v "$(pwd)/output:/benchmark/output" flamapy-benchmark

# Quick smoke test (10 models, no FaMA, 30 s timeout)
docker run --rm -v "$(pwd)/output:/benchmark/output" flamapy-benchmark \
    python main.py run \
        --max-models 10 --timeout 30 --no-fama \
        --output output/smoke_test.csv
```

### Option B — Manual (Linux / macOS)

Python 3.9+ is required. `SIGALRM`-based timeouts do not work on Windows.

```bash
pip install -r requirements.txt
python main.py run                                    # all solvers, default 60 s timeout
python main.py run --max-models 10 --timeout 30      # quick smoke test
python main.py plots                                  # regenerate figures
python main.py --help                                 # list all commands
```

To enable FaMA, build the JAR first:

```bash
# Needs JDK 11+ and Maven 3.6+
git clone https://github.com/diverso-lab/FaMA fama_src
cd fama_src && mvn install -DskipTests -q
cd ../fama_cli && mvn package -DskipTests -q

python main.py run \
    --fama-jar fama_cli/target/fama-cli-1.0.0-jar-with-dependencies.jar
```

---

## CLI options

| Flag | Default | Description |
|------|---------|-------------|
| `--zip` | `uvlhub_bulk_2026_03_13.zip` | UVL model archive |
| `--timeout` | `60` | Per-operation timeout (seconds) |
| `--max-models` | `0` (all) | Stop after N models |
| `--output` | `benchmark_results.csv` | Output CSV path |
| `--pysat-solvers` | all six | Comma-separated PySAT backends |
| `--no-pysat` / `--no-bdd` / `--no-z3` / `--no-fama` | off | Disable solver |
| `--fama-jar` | _(disabled)_ | Path to FaMA fat JAR; enables FaMA |
| `--fama-solvers` | `Choco,JaCoP,Sat4j` | FaMA solver IDs |
| `--workers` | `1` | Parallel workers (experimental) |

---

## Regenerating plots

```bash
python main.py plots --csv results/flamapy_benchmark_2026.csv --out plots/
```

---

## Output CSV format

Each row is one `(model, solver, operation)` measurement.

| Column | Description |
|--------|-------------|
| `model_name` | UVL filename |
| `num_features` | Number of features |
| `num_constraints` | Number of cross-tree constraints |
| `solver` | `pysat`, `bdd`, `z3`, or `fama` |
| `solver_variant` | PySAT/FaMA backend; `n/a` for BDD/Z3 |
| `operation` | Operation name |
| `time_seconds` | Wall-clock time |
| `status` | `success`, `timeout`, or `error` |
| `timeout_reached` | `True` if the timeout fired |
| `result_summary` | Short string representation of the result |

Results are written incrementally — a crash mid-run does not lose earlier rows.

---

## Repository contents

```
main.py                   — CLI entry point (run / plots)
scripts/
  benchmark.py            — solver benchmark logic
  fama_xml.py             — UVL → FAMA XML converter
  generate_plots.py       — paper figure generation
run_benchmark.sh          — one-shot shell runner (non-Docker path)
Dockerfile                — reproducible environment (Python + JDK + Maven)
requirements.txt          — Python dependencies
uvlhub_bulk_2026_03_13.zip — 1,611 UVL feature models from UVLHub
results/                  — pre-computed benchmark CSVs
plots/                    — pre-generated paper figures (PDF + PNG)
fama_cli/                 — Maven project: thin Java CLI wrapper for FaMA
```

---

## Dataset

`uvlhub_bulk_2026_03_13.zip` is a bulk export from [UVLHub](https://www.uvlhub.io/) containing 1,611 UVL feature models (53 dataset groups). Models range from small toy examples to large industrial models (Linux kernel, BusyBox, automotive).

Of the 1,611 models, **1,099 pass the lossless UVL→FAMA XML conversion** and are included in FaMA comparisons. The remaining 512 use constraint forms that FaMA XML cannot represent and are automatically skipped (recorded as `__skipped_lossy__` in the CSV).

---

## Platform note

The per-operation timeout relies on `signal.SIGALRM` and therefore requires **Linux or macOS**. The Docker image uses `python:3.11-slim` (Debian-based Linux) and works on any host OS that runs Docker.
