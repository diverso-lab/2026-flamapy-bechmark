#!/usr/bin/env python3
"""
Flamapy Solver Benchmark
========================
Compares PySAT (multiple SAT solver backends), BDD, Z3, and (optionally) the
legacy Java FaMA tool across UVL benchmark models.

Each operation is run with a configurable per-operation timeout.
Results are written to a CSV file for further analysis.

Usage:
    python benchmark.py --zip uvlhub_bulk_2026_03_13.zip --timeout 60 --output results.csv
    python benchmark.py --zip uvlhub_bulk_2026_03_13.zip --timeout 30 --max-models 10
    python benchmark.py --zip uvlhub_bulk_2026_03_13.zip --pysat-solvers glucose3,minisat22
    python benchmark.py --fama-jar fama_cli/target/fama-cli-jar-with-dependencies.jar
    python benchmark.py --help

FaMA integration
----------------
FaMA is an old Java-based feature model analyser with three constraint solvers:
  * Choco   – Choco2 constraint-programming solver
  * Sat4j   – SAT-based solver

To enable FaMA benchmarking:
  1. Build fama_cli/ (see fama_cli/pom.xml and build_fama.sh for instructions).
  2. Pass the resulting fat JAR with --fama-jar.

UVL models are automatically converted to the FAMA XML format on the fly using
fama_xml.py.  Complex cross-tree constraints that cannot be expressed as
simple binary requires/excludes are dropped with a warning.
"""

import argparse
import concurrent.futures
import csv
import logging
import multiprocessing
import os
import queue as _queue_module
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable, Optional

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Available PySAT solver backends
# ---------------------------------------------------------------------------
ALL_PYSAT_SOLVERS = [
    'glucose3',
    'glucose4',
    'minisat22',
    'lingeling',
    'maplesat',
    'cadical153',
]

# Operations that need no extra parameters and are safe to run blindly
PYSAT_OPERATIONS = [
    'Satisfiable',
    'CoreFeatures',
    'DeadFeatures',
    'FalseOptionalFeatures',
    'ConfigurationsNumber',
]

BDD_OPERATIONS = [
    'Satisfiable',
    'CoreFeatures',
    'DeadFeatures',
    'FalseOptionalFeatures',
    'ConfigurationsNumber',
    'VariantFeatures',
    'PureOptionalFeatures',
    'UniqueFeatures',
    'Variability',
    'CommonalityFactor',
    'Homogeneity',
    'ProductDistribution',
    'FeatureInclusionProbability',
    'ConfigurationsWithNFeatures',
]

Z3_OPERATIONS = [
    'Satisfiable',
    'CoreFeatures',
    'DeadFeatures',
    'FalseOptionalFeatures',
    'ConfigurationsNumber',
    'AllFeatureBounds',
]

# ---------------------------------------------------------------------------
# FaMA (Java) solver configuration
# ---------------------------------------------------------------------------
ALL_FAMA_SOLVERS = [
    'Choco',    # Choco2 constraint solver
    'Sat4j',    # SAT-based solver
]

# FaMA question IDs (must match FaMaConfig.xml registrations)
FAMA_OPERATIONS = [
    'Valid',         # → Satisfiable equivalent
    '#Products',     # → ConfigurationsNumber equivalent
    'DetectErrors',  # → dead / false-optional features
    'Variability',   # normalised variability ratio
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class Result:
    model_name: str
    num_features: int
    num_constraints: int
    solver: str          # 'pysat', 'bdd', 'z3'
    solver_variant: str  # e.g. 'glucose3', 'n/a'
    operation: str
    time_seconds: float
    status: str          # 'success' | 'timeout' | 'error' | 'skip'
    timeout_reached: bool  # True when the per-operation timeout fired
    result_summary: str  # brief string of the result value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _summarize(value: Any, max_len: int = 80) -> str:
    """Turn an operation result into a short string."""
    if value is None:
        return 'None'
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        n = len(value)  # type: ignore[arg-type]
        return f'[{n} items]'
    if isinstance(value, dict):
        return f'{{{len(value)} entries}}'
    s = str(value)
    return s[:max_len] + '...' if len(s) > max_len else s


class _OperationTimeout(Exception):
    pass


def _alarm_handler(signum: int, frame: Any) -> None:
    raise _OperationTimeout()


def _run_with_sigalrm(func: Any, timeout_secs: int) -> tuple[str, float, bool, str]:
    """
    Run func() with a SIGALRM-based timeout.
    Returns (status, elapsed_seconds, timeout_reached, result_summary).
    Only works on Unix/macOS.
    """
    old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(timeout_secs)
    t0 = time.perf_counter()
    try:
        result = func()
        elapsed = time.perf_counter() - t0
        signal.alarm(0)
        return 'success', elapsed, False, _summarize(result)
    except _OperationTimeout:
        elapsed = time.perf_counter() - t0
        return 'timeout', elapsed, True, ''
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        signal.alarm(0)
        return 'error', elapsed, False, str(exc)[:120]
    finally:
        signal.signal(signal.SIGALRM, old_handler)
        signal.alarm(0)


# ---------------------------------------------------------------------------
# Per-solver benchmark functions (run inside subprocess)
# ---------------------------------------------------------------------------

def _bench_pysat(fm: Any, solver_name: str, timeout: int, skip_ops: set[str] | None = None) -> list[tuple[str, str, float, bool, str]]:
    """
    Benchmark PySAT operations.
    Returns list of (operation_name, status, elapsed, timeout_reached, result_summary).
    """
    from flamapy.metamodels.pysat_metamodel.transformations import FmToPysat
    from flamapy.metamodels.pysat_metamodel.operations import (
        PySATSatisfiable,
        PySATCoreFeatures,
        PySATDeadFeatures,
        PySATFalseOptionalFeatures,
        PySATConfigurationsNumber,
    )
    from pysat.solvers import Solver as PySolver

    # Transform
    status, elapsed, timed_out, err = _run_with_sigalrm(
        lambda: FmToPysat(fm).transform(), timeout
    )
    if status != 'success':
        return [('__transform__', status, elapsed, timed_out, err)]
    pysat_model = FmToPysat(fm).transform()  # already fast after import warm-up

    op_map = {
        'Satisfiable': PySATSatisfiable,
        'CoreFeatures': PySATCoreFeatures,
        'DeadFeatures': PySATDeadFeatures,
        'FalseOptionalFeatures': PySATFalseOptionalFeatures,
        'ConfigurationsNumber': PySATConfigurationsNumber,
    }

    results = []
    for op_name, OpClass in op_map.items():
        if skip_ops and op_name in skip_ops:
            continue
        op = OpClass()
        # Override the default glucose3 solver with the requested one
        if hasattr(op, 'solver'):
            op.solver = PySolver(name=solver_name)

        def _run(op: Any = op, model: Any = pysat_model) -> Any:
            op.execute(model)
            return op.get_result()

        status, elapsed, timed_out, summary = _run_with_sigalrm(_run, timeout)
        results.append((op_name, status, elapsed, timed_out, summary))

    return results


def _bench_bdd(fm: Any, timeout: int, skip_ops: set[str] | None = None) -> list[tuple[str, str, float, str]]:
    """Benchmark BDD operations."""
    from flamapy.metamodels.bdd_metamodel.transformations import FmToBDD
    from flamapy.metamodels.bdd_metamodel.operations import (
        BDDSatisfiable,
        BDDCoreFeatures,
        BDDDeadFeatures,
        BDDFalseOptionalFeatures,
        BDDConfigurationsNumber,
        BDDVariantFeatures,
        BDDPureOptionalFeatures,
        BDDUniqueFeatures,
        BDDVariability,
        BDDCommonalityFactor,
        BDDHomogeneity,
        BDDProductDistribution,
        BDDFeatureInclusionProbability,
        BDDConfigurationsWithNFeatures,
    )

    # BDD compilation can be expensive — guard with timeout
    bdd_model_holder: list[Any] = []

    def _transform() -> Any:
        model = FmToBDD(fm).transform()
        bdd_model_holder.append(model)
        return model

    status, elapsed, timed_out, err = _run_with_sigalrm(_transform, timeout)
    if status != 'success':
        return [('__transform__', status, elapsed, timed_out, err)]
    bdd_model = bdd_model_holder[0]

    op_map = {
        'Satisfiable': BDDSatisfiable,
        'CoreFeatures': BDDCoreFeatures,
        'DeadFeatures': BDDDeadFeatures,
        'FalseOptionalFeatures': BDDFalseOptionalFeatures,
        'ConfigurationsNumber': BDDConfigurationsNumber,
        'VariantFeatures': BDDVariantFeatures,
        'PureOptionalFeatures': BDDPureOptionalFeatures,
        'UniqueFeatures': BDDUniqueFeatures,
        'Variability': BDDVariability,
        'CommonalityFactor': BDDCommonalityFactor,
        'Homogeneity': BDDHomogeneity,
        'ProductDistribution': BDDProductDistribution,
        'FeatureInclusionProbability': BDDFeatureInclusionProbability,
        'ConfigurationsWithNFeatures': BDDConfigurationsWithNFeatures,
    }

    results = []
    for op_name, OpClass in op_map.items():
        if skip_ops and op_name in skip_ops:
            continue
        op = OpClass()

        def _run(op: Any = op, model: Any = bdd_model) -> Any:
            op.execute(model)
            return op.get_result()

        status, elapsed, timed_out, summary = _run_with_sigalrm(_run, timeout)
        results.append((op_name, status, elapsed, timed_out, summary))

    return results


def _bench_z3(fm: Any, timeout: int, skip_ops: set[str] | None = None) -> list[tuple[str, str, float, bool, str]]:
    """Benchmark Z3 operations."""
    from flamapy.metamodels.z3_metamodel.transformations import FmToZ3
    from flamapy.metamodels.z3_metamodel.operations import (
        Z3Satisfiable,
        Z3CoreFeatures,
        Z3DeadFeatures,
        Z3FalseOptionalFeatures,
        Z3ConfigurationsNumber,
        Z3AllFeatureBounds,
    )

    z3_model_holder: list[Any] = []

    def _transform() -> Any:
        model = FmToZ3(fm).transform()
        z3_model_holder.append(model)
        return model

    status, elapsed, timed_out, err = _run_with_sigalrm(_transform, timeout)
    if status != 'success':
        return [('__transform__', status, elapsed, timed_out, err)]
    z3_model = z3_model_holder[0]

    op_map = {
        'Satisfiable': Z3Satisfiable,
        'CoreFeatures': Z3CoreFeatures,
        'DeadFeatures': Z3DeadFeatures,
        'FalseOptionalFeatures': Z3FalseOptionalFeatures,
        'ConfigurationsNumber': Z3ConfigurationsNumber,
        'AllFeatureBounds': Z3AllFeatureBounds,
    }

    results = []
    for op_name, OpClass in op_map.items():
        if skip_ops and op_name in skip_ops:
            continue
        op = OpClass()

        def _run(op: Any = op, model: Any = z3_model) -> Any:
            op.execute(model)
            return op.get_result()

        status, elapsed, timed_out, summary = _run_with_sigalrm(_run, timeout)
        results.append((op_name, status, elapsed, timed_out, summary))

    return results


# ---------------------------------------------------------------------------
# FaMA (Java) benchmark – converts UVL→FAMA XML, then shells out to Java
# ---------------------------------------------------------------------------

def _bench_fama(
    fm: Any,
    solver_name: str,
    fama_jar: str,
    tmp_dir: str,
    timeout: int,
    skip_ops: set[str] | None = None,
) -> list[tuple[str, str, float, bool, str]]:
    """Benchmark FaMA operations for a single solver.

    Converts the flamapy feature model to FAMA XML (via fama_converter),
    then calls the FamaCLI fat JAR once per operation using subprocess.

    Returns list of (operation_name, status, elapsed, timeout_reached, result_summary).
    """
    from .fama_xml import fm_to_fama_xml

    # ---- Convert UVL → FAMA XML ----
    model_name = getattr(fm.root, 'name', 'model')
    safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in model_name)
    fama_xml_path = os.path.join(tmp_dir, f'{safe_name}_{solver_name}.fama')

    dropped_ctcs: list[str] = []

    def _convert() -> Any:
        warns = fm_to_fama_xml(fm, fama_xml_path)
        dropped_ctcs.extend(warns)
        return warns  # list of warning strings

    status, elapsed, timed_out, err = _run_with_sigalrm(_convert, timeout)
    if status != 'success':
        return [('__transform__', status, elapsed, timed_out, err)]

    # Skip models where the UVL→FaMA XML conversion was lossy (complex CTCs
    # not representable in FaMA XML were dropped).  Running FaMA on a simplified
    # model would produce results that are not comparable to flamapy's analysis
    # on the full model.
    if dropped_ctcs:
        try:
            os.remove(fama_xml_path)
        except OSError:
            pass
        return [('__skipped_lossy__', 'skip', elapsed, False,
                 f'{len(dropped_ctcs)} CTC(s) not representable in FaMA XML')]

    results = []
    for op_name in FAMA_OPERATIONS:
        if skip_ops and op_name in skip_ops:
            continue
        t0 = time.perf_counter()
        timed_out_flag = False
        op_status = 'error'
        summary = ''
        try:
            # Use Popen + start_new_session so we can kill the entire process
            # group (JVM may spawn threads that keep stdout/stderr open, which
            # would cause subprocess.run's post-kill communicate() to hang).
            proc = subprocess.Popen(
                ['java', '-jar', fama_jar, fama_xml_path, solver_name, op_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.communicate()  # drain pipes after kill (fast: process is dead)
                elapsed_op = time.perf_counter() - t0
                timed_out_flag = True
                op_status = 'timeout'
                summary = ''
                results.append((op_name, op_status, elapsed_op, timed_out_flag, summary))
                continue

            elapsed_op = time.perf_counter() - t0
            stdout = stdout.strip()
            stderr = stderr.strip()

            if proc.returncode == 0 and stdout.startswith('RESULT:'):
                op_status = 'success'
                summary = stdout[len('RESULT:'):].strip()[:120]
            elif stdout.startswith('ERROR:'):
                op_status = 'error'
                summary = stdout[len('ERROR:'):].strip()[:120]
            else:
                op_status = 'error'
                msg = stdout or stderr
                summary = msg[:120]

        except FileNotFoundError:
            elapsed_op = time.perf_counter() - t0
            op_status = 'error'
            summary = 'java not found – is JRE installed and on PATH?'
        except Exception as exc:
            elapsed_op = time.perf_counter() - t0
            op_status = 'error'
            summary = str(exc)[:120]

        results.append((op_name, op_status, elapsed_op, timed_out_flag, summary))

    # Clean up temp file
    try:
        os.remove(fama_xml_path)
    except OSError:
        pass

    return results


# ---------------------------------------------------------------------------
# Subprocess worker: benchmarks one model across all configured solvers
# ---------------------------------------------------------------------------

def _model_worker(
    queue: multiprocessing.Queue,  # type: ignore[type-arg]
    model_path: str,
    pysat_solvers: list[str],
    enable_bdd: bool,
    enable_z3: bool,
    timeout: int,
    min_features: int = 0,
    max_features: int = 0,
    fama_jar: str = '',
    fama_solvers: list[str] = [],
    fama_tmp_dir: str = '',
    completed: frozenset[tuple[str, str, str, str]] = frozenset(),
) -> None:
    """
    Runs in a subprocess. Loads one UVL model and benchmarks all solvers.
    Streams each Result individually via queue; sends None sentinel when done.
    Operations already present in `completed` are skipped.
    """
    # Silence noisy loggers inside the subprocess
    logging.disable(logging.WARNING)

    from flamapy.metamodels.fm_metamodel.transformations import UVLReader

    model_name = Path(model_path).name

    # --- Load FM ---
    try:
        fm = UVLReader(model_path).transform()
        features = fm.get_features()
        num_features = len(list(features))
        constraints = fm.get_constraints()
        num_constraints = len(list(constraints))
    except Exception as exc:
        queue.put(Result(
            model_name=model_name,
            num_features=-1,
            num_constraints=-1,
            solver='load',
            solver_variant='n/a',
            operation='__load__',
            time_seconds=0.0,
            status='error',
            timeout_reached=False,
            result_summary=str(exc)[:120],
        ))
        queue.put(None)
        return

    # --- Feature count filter ---
    if (min_features > 0 and num_features < min_features) or \
            (max_features > 0 and num_features > max_features):
        queue.put('__skipped__')
        return

    def _done(solver: str, variant: str, op: str) -> bool:
        return (model_name, solver, variant, op) in completed

    # --- PySAT ---
    for solver_name in pysat_solvers:
        skip_ops = {op for op in PYSAT_OPERATIONS if _done('pysat', solver_name, op)}
        try:
            op_results = _bench_pysat(fm, solver_name, timeout, skip_ops=skip_ops)
        except Exception as exc:
            op_results = [('__bench__', 'error', 0.0, False, str(exc)[:120])]

        for op_name, status, elapsed, timed_out, summary in op_results:
            queue.put(Result(
                model_name=model_name,
                num_features=num_features,
                num_constraints=num_constraints,
                solver='pysat',
                solver_variant=solver_name,
                operation=op_name,
                time_seconds=round(elapsed, 4),
                status=status,
                timeout_reached=timed_out,
                result_summary=summary,
            ))

    # --- BDD ---
    if enable_bdd:
        skip_ops = {op for op in BDD_OPERATIONS if _done('bdd', 'n/a', op)}
        try:
            op_results = _bench_bdd(fm, timeout, skip_ops=skip_ops)
        except Exception as exc:
            op_results = [('__bench__', 'error', 0.0, False, str(exc)[:120])]

        for op_name, status, elapsed, timed_out, summary in op_results:
            queue.put(Result(
                model_name=model_name,
                num_features=num_features,
                num_constraints=num_constraints,
                solver='bdd',
                solver_variant='n/a',
                operation=op_name,
                time_seconds=round(elapsed, 4),
                status=status,
                timeout_reached=timed_out,
                result_summary=summary,
            ))

    # --- Z3 ---
    if enable_z3:
        skip_ops = {op for op in Z3_OPERATIONS if _done('z3', 'n/a', op)}
        try:
            op_results = _bench_z3(fm, timeout, skip_ops=skip_ops)
        except Exception as exc:
            op_results = [('__bench__', 'error', 0.0, False, str(exc)[:120])]

        for op_name, status, elapsed, timed_out, summary in op_results:
            queue.put(Result(
                model_name=model_name,
                num_features=num_features,
                num_constraints=num_constraints,
                solver='z3',
                solver_variant='n/a',
                operation=op_name,
                time_seconds=round(elapsed, 4),
                status=status,
                timeout_reached=timed_out,
                result_summary=summary,
            ))

    # --- FaMA ---
    if fama_jar and fama_solvers:
        for solver_name in fama_solvers:
            skip_ops = {op for op in FAMA_OPERATIONS if _done('fama', solver_name, op)}
            try:
                op_results = _bench_fama(fm, solver_name, fama_jar, fama_tmp_dir, timeout, skip_ops=skip_ops)
            except Exception as exc:
                op_results = [('__bench__', 'error', 0.0, False, str(exc)[:120])]

            for op_name, status, elapsed, timed_out, summary in op_results:
                queue.put(Result(
                    model_name=model_name,
                    num_features=num_features,
                    num_constraints=num_constraints,
                    solver='fama',
                    solver_variant=solver_name,
                    operation=op_name,
                    time_seconds=round(elapsed, 4),
                    status=status,
                    timeout_reached=timed_out,
                    result_summary=summary,
                ))

    queue.put(None)  # sentinel: subprocess done


# ---------------------------------------------------------------------------
# ZIP extraction helpers
# ---------------------------------------------------------------------------

def extract_uvl_files(zip_path: str, extract_dir: str) -> list[str]:
    """Extract all .uvl files from a zip archive and return their paths."""
    uvl_paths: list[str] = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for member in zf.namelist():
            if member.lower().endswith('.uvl') and not member.startswith('__MACOSX'):
                target = os.path.join(extract_dir, member)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, 'wb') as dst:
                    dst.write(src.read())
                uvl_paths.append(target)
    return uvl_paths


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def load_completed(output_path: str) -> set[tuple[str, str, str, str]]:
    """Read an existing CSV and return the set of (model_name, solver, solver_variant, operation) rows."""
    completed: set[tuple[str, str, str, str]] = set()
    if not Path(output_path).exists():
        return completed
    try:
        with open(output_path, newline='') as f:
            for row in csv.DictReader(f):
                completed.add((row['model_name'], row['solver'], row['solver_variant'], row['operation']))
    except Exception:
        pass
    return completed


def init_csv(output_path: str) -> None:
    """Write the CSV header only if the file does not already exist or is empty."""
    path = Path(output_path)
    if path.exists() and path.stat().st_size > 0:
        return
    fieldnames = [f.name for f in fields(Result)]
    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()


def append_csv_row(result: Result, output_path: str) -> None:
    """Append a single result row to an already-initialised CSV file."""
    fieldnames = [f.name for f in fields(Result)]
    with open(output_path, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writerow({
            'model_name': result.model_name,
            'num_features': result.num_features,
            'num_constraints': result.num_constraints,
            'solver': result.solver,
            'solver_variant': result.solver_variant,
            'operation': result.operation,
            'time_seconds': result.time_seconds,
            'status': result.status,
            'timeout_reached': 'True' if result.timeout_reached else 'False',
            'result_summary': result.result_summary,
        })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Flamapy solver benchmark — compare PySAT, BDD, and Z3 across UVL models.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        '--zip',
        default='uvlhub_bulk_2026_03_13.zip',
        help='Path to the ZIP archive containing UVL models (default: uvlhub_bulk_2026_03_13.zip)',
    )
    parser.add_argument(
        '--extract-dir',
        default='.benchmark_models',
        help='Directory to extract models into (default: .benchmark_models)',
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=60,
        help='Per-operation timeout in seconds (default: 60)',
    )
    parser.add_argument(
        '--max-models',
        type=int,
        default=0,
        help='Maximum number of models to benchmark (0 = all, default: 0)',
    )
    parser.add_argument(
        '--min-features',
        type=int,
        default=0,
        help='Only benchmark models with at least this many features (0 = no minimum, default: 0)',
    )
    parser.add_argument(
        '--max-features',
        type=int,
        default=0,
        help='Only benchmark models with at most this many features (0 = no maximum, default: 0)',
    )
    parser.add_argument(
        '--output',
        default='benchmark_results.csv',
        help='Output CSV file path (default: benchmark_results.csv)',
    )
    parser.add_argument(
        '--pysat-solvers',
        default=','.join(ALL_PYSAT_SOLVERS),
        help=(
            'Comma-separated list of PySAT solver backends to use '
            f'(default: {",".join(ALL_PYSAT_SOLVERS)})'
        ),
    )
    parser.add_argument(
        '--no-bdd',
        action='store_true',
        help='Skip BDD solver',
    )
    parser.add_argument(
        '--no-z3',
        action='store_true',
        help='Skip Z3 solver',
    )
    parser.add_argument(
        '--no-pysat',
        action='store_true',
        help='Skip all PySAT solvers',
    )
    parser.add_argument(
        '--workers',
        type=int,
        default=1,
        help='Number of parallel model workers (default: 1). Use with care on large models.',
    )

    # ---- FaMA (Java) options ----
    fama_group = parser.add_argument_group(
        'FaMA (Java)',
        'Options for the legacy Java FaMA tool.  FaMA benchmarking is disabled by default '
        'and is activated by supplying --fama-jar.',
    )
    fama_group.add_argument(
        '--fama-jar',
        default='',
        metavar='PATH',
        help=(
            'Path to the FamaCLI fat JAR (fama_cli/target/fama-cli-jar-with-dependencies.jar). '
            'If not supplied FaMA benchmarking is skipped.'
        ),
    )
    fama_group.add_argument(
        '--fama-solvers',
        default=','.join(ALL_FAMA_SOLVERS),
        help=(
            'Comma-separated list of FaMA solver IDs to use '
            f'(default: {",".join(ALL_FAMA_SOLVERS)})'
        ),
    )
    fama_group.add_argument(
        '--no-fama',
        action='store_true',
        help='Explicitly disable FaMA benchmarking (overrides --fama-jar).',
    )
    return parser.parse_args()


def _timeout_result_row(
    model_path: str,
    solvers: list[str],
    enable_bdd: bool,
    enable_z3: bool,
    fama_solvers: list[str] = [],
) -> list[Result]:
    """Generate timeout placeholder rows when a model worker is killed."""
    model_name = Path(model_path).name
    rows = []

    def _row(solver: str, variant: str, op: str) -> Result:
        return Result(
            model_name=model_name,
            num_features=-1,
            num_constraints=-1,
            solver=solver,
            solver_variant=variant,
            operation=op,
            time_seconds=0.0,
            status='timeout',
            timeout_reached=True,
            result_summary='model-level timeout',
        )

    for s in solvers:
        for op in PYSAT_OPERATIONS:
            rows.append(_row('pysat', s, op))
    if enable_bdd:
        for op in BDD_OPERATIONS:
            rows.append(_row('bdd', 'n/a', op))
    if enable_z3:
        for op in Z3_OPERATIONS:
            rows.append(_row('z3', 'n/a', op))
    for s in fama_solvers:
        for op in FAMA_OPERATIONS:
            rows.append(_row('fama', s, op))
    return rows


def _run_one_model(
    ctx: Any,
    model_path: str,
    pysat_solvers: list[str],
    enable_bdd: bool,
    enable_z3: bool,
    op_timeout: int,
    model_timeout: int,
    min_features: int = 0,
    max_features: int = 0,
    fama_jar: str = '',
    fama_solvers: list[str] = [],
    fama_tmp_dir: str = '',
    on_result: Optional[Callable[[Result], None]] = None,
    completed: frozenset[tuple[str, str, str, str]] = frozenset(),
) -> list[Result]:
    """Spawn a worker subprocess for one model and stream results via on_result callback."""
    model_name = Path(model_path).name
    q: multiprocessing.Queue = ctx.Queue()  # type: ignore[type-arg]
    proc = ctx.Process(
        target=_model_worker,
        args=(q, model_path, pysat_solvers, enable_bdd, enable_z3, op_timeout,
              min_features, max_features, fama_jar, fama_solvers, fama_tmp_dir, completed),
    )
    proc.start()

    model_results: list[Result] = []
    deadline = time.monotonic() + model_timeout
    done = False

    while not done:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            item = q.get(timeout=min(remaining, 1.0))
            if item is None:  # sentinel: subprocess finished normally
                done = True
            elif item == '__skipped__':
                logger.info(f'  -> {model_name}: skipped (feature count outside filter range)')
                proc.join()
                return []
            else:
                model_results.append(item)
                if on_result is not None:
                    on_result(item)
        except _queue_module.Empty:
            if not proc.is_alive():
                # Process exited without sentinel — drain any buffered items
                while True:
                    try:
                        item = q.get_nowait()
                        if item is None:
                            done = True
                            break
                        if isinstance(item, Result):
                            model_results.append(item)
                            if on_result is not None:
                                on_result(item)
                    except _queue_module.Empty:
                        break
                break

    if proc.is_alive():
        logger.warning(f'  -> Model-level timeout reached, terminating worker for {model_name}')
        proc.terminate()
        proc.join()
        if not model_results:
            placeholders = _timeout_result_row(model_path, pysat_solvers, enable_bdd, enable_z3, fama_solvers)
            for r in placeholders:
                model_results.append(r)
                if on_result is not None:
                    on_result(r)
        return model_results

    proc.join()

    if not model_results:
        logger.warning(f'  -> Worker exited without results for {model_name}')
        return []

    success = sum(1 for r in model_results if r.status == 'success')
    timeouts = sum(1 for r in model_results if r.status == 'timeout')
    errors = sum(1 for r in model_results if r.status == 'error')
    logger.info(
        f'  -> {model_name}: {len(model_results)} results: '
        f'{success} success, {timeouts} timeout, {errors} error'
    )
    return model_results


def main() -> None:
    args = parse_args()

    # Resolve paths: absolute paths stay as-is, relative paths resolve from CWD.
    # However, the zip default is relative to the script's directory for convenience.
    script_dir = Path(__file__).parent
    cwd = Path.cwd()

    if Path(args.zip).is_absolute():
        zip_path = Path(args.zip)
    elif Path(args.zip).exists():
        zip_path = Path(args.zip).resolve()
    else:
        zip_path = script_dir / args.zip

    extract_dir = Path(args.extract_dir).resolve() if not Path(args.extract_dir).is_absolute() else Path(args.extract_dir)
    output_path = Path(args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)

    if not zip_path.exists():
        logger.error(f'ZIP file not found: {zip_path}')
        sys.exit(1)

    # Resolve solver config
    pysat_solvers = [] if args.no_pysat else [s.strip() for s in args.pysat_solvers.split(',') if s.strip()]
    enable_bdd = not args.no_bdd
    enable_z3 = not args.no_z3

    # FaMA config
    fama_jar = ''
    fama_solvers: list[str] = []
    fama_tmp_dir = ''
    if not args.no_fama and args.fama_jar:
        fama_jar_path = Path(args.fama_jar)
        if not fama_jar_path.exists():
            logger.error(f'FaMA JAR not found: {fama_jar_path}')
            sys.exit(1)
        fama_jar = str(fama_jar_path.resolve())
        fama_solvers = [s.strip() for s in args.fama_solvers.split(',') if s.strip()]
        # Create a shared temp dir for FAMA XML files (cleaned up after the run)
        fama_tmp_dir = tempfile.mkdtemp(prefix='fama_xml_')

    logger.info(f'ZIP: {zip_path}')
    logger.info(f'Timeout: {args.timeout}s per operation')
    logger.info(f'PySAT solvers: {pysat_solvers}')
    logger.info(f'BDD: {"enabled" if enable_bdd else "disabled"}')
    logger.info(f'Z3:  {"enabled" if enable_z3 else "disabled"}')
    if fama_jar:
        logger.info(f'FaMA JAR: {fama_jar}')
        logger.info(f'FaMA solvers: {fama_solvers}')
    else:
        logger.info('FaMA: disabled (use --fama-jar to enable)')
    if args.min_features > 0:
        logger.info(f'Min features filter: {args.min_features}')
    if args.max_features > 0:
        logger.info(f'Max features filter: {args.max_features}')

    # Extract models
    logger.info(f'Extracting UVL files to {extract_dir} ...')
    extract_dir.mkdir(parents=True, exist_ok=True)
    uvl_files = extract_uvl_files(str(zip_path), str(extract_dir))
    logger.info(f'Found {len(uvl_files)} UVL model(s)')

    if not uvl_files:
        logger.error('No .uvl files found in the archive.')
        sys.exit(1)

    if args.max_models > 0:
        uvl_files = uvl_files[:args.max_models]
        logger.info(f'Limited to {len(uvl_files)} model(s) via --max-models')

    # Model-level timeout: generous budget so the subprocess can finish
    model_timeout = args.timeout * (
        len(pysat_solvers) * len(PYSAT_OPERATIONS)
        + (len(BDD_OPERATIONS) if enable_bdd else 0)
        + (len(Z3_OPERATIONS) if enable_z3 else 0)
        + len(fama_solvers) * len(FAMA_OPERATIONS)
    ) + 60  # extra padding

    all_results: list[Result] = []
    total = len(uvl_files)
    results_lock = threading.Lock()

    ctx = multiprocessing.get_context('spawn')
    logger.info(f'Workers: {args.workers}')

    # Load already-completed operations from an existing CSV (resume support)
    completed = frozenset(load_completed(str(output_path)))
    if completed:
        logger.info(f'Resuming: {len(completed)} operation row(s) already in CSV will be skipped')

    # Initialise the CSV file (write header only if file is new)
    init_csv(str(output_path))

    # Pre-compute the full expected (solver, variant, operation) set per model
    # to cheaply detect models that are already fully done.
    expected_ops: set[tuple[str, str, str]] = (
        {('pysat', s, op) for s in pysat_solvers for op in PYSAT_OPERATIONS}
        | ({('bdd', 'n/a', op) for op in BDD_OPERATIONS} if enable_bdd else set())
        | ({('z3', 'n/a', op) for op in Z3_OPERATIONS} if enable_z3 else set())
        | {('fama', s, op) for s in fama_solvers for op in FAMA_OPERATIONS}
    )
    # Build per-model index for O(1) lookup
    completed_by_model: dict[str, set[tuple[str, str, str]]] = {}
    for mn, sv, svv, op in completed:
        completed_by_model.setdefault(mn, set()).add((sv, svv, op))

    # Total pending operations across all models for the ops progress bar
    total_ops = total * len(expected_ops) - len(completed)

    model_bar = tqdm(
        total=total,
        desc="Models",
        unit="model",
        dynamic_ncols=True,
    )
    ops_bar = tqdm(
        total=max(total_ops, 0),
        desc="Operations",
        unit="op",
        leave=False,
        dynamic_ncols=True,
    )

    def _process_model(item: tuple[int, str]) -> None:
        idx, model_path = item
        model_name = Path(model_path).name

        # Skip entirely if every expected operation is already in the CSV
        if expected_ops and expected_ops <= completed_by_model.get(model_name, set()):
            logger.info(f'[{idx}/{total}] Skipping {model_name} (all {len(expected_ops)} operations already in CSV)')
            model_bar.update(1)
            model_bar.set_postfix(model=model_name, status="skipped")
            return

        logger.info(f'[{idx}/{total}] Benchmarking {model_name} ...')
        model_bar.set_postfix(model=model_name)

        def on_result(r: Result) -> None:
            with results_lock:
                all_results.append(r)
                append_csv_row(r, str(output_path))
            ops_bar.update(1)
            variant = f'/{r.solver_variant}' if r.solver_variant != 'n/a' else ''
            logger.info(
                f'  [{r.model_name}] {r.solver}{variant} | {r.operation} '
                f'-> {r.status} ({r.time_seconds:.4f}s)'
            )

        _run_one_model(
            ctx, model_path, pysat_solvers, enable_bdd, enable_z3, args.timeout, model_timeout,
            args.min_features, args.max_features,
            fama_jar, fama_solvers, fama_tmp_dir,
            on_result=on_result,
            completed=completed,
        )
        model_bar.update(1)

    with logging_redirect_tqdm():
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            executor.map(_process_model, enumerate(uvl_files, start=1))

    model_bar.close()
    ops_bar.close()

    # Clean up FaMA temp directory
    if fama_tmp_dir and os.path.isdir(fama_tmp_dir):
        import shutil
        shutil.rmtree(fama_tmp_dir, ignore_errors=True)

    logger.info(f'Done. {len(all_results)} result rows written to {output_path}')
    _print_summary(all_results)


def _print_summary(results: list[Result]) -> None:
    """Print a brief text summary to stdout."""
    if not results:
        return

    print('\n' + '=' * 70)
    print('BENCHMARK SUMMARY')
    print('=' * 70)

    # Group by solver+variant
    from collections import defaultdict
    by_solver: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        key = f'{r.solver}/{r.solver_variant}' if r.solver_variant != 'n/a' else r.solver
        by_solver[key].append(r)

    for solver_key, rows in sorted(by_solver.items()):
        success = [r for r in rows if r.status == 'success']
        timeouts = [r for r in rows if r.status == 'timeout']
        errors = [r for r in rows if r.status == 'error']
        times = [r.time_seconds for r in success]
        avg_time = sum(times) / len(times) if times else 0.0
        print(f'\n  Solver: {solver_key}')
        print(f'    Success : {len(success)}')
        print(f'    Timeout : {len(timeouts)}')
        print(f'    Error   : {len(errors)}')
        if times:
            print(f'    Avg time: {avg_time:.3f}s  (min={min(times):.3f}s  max={max(times):.3f}s)')

    print()


if __name__ == '__main__':
    # Required for multiprocessing on macOS/Windows with 'spawn' start method
    multiprocessing.freeze_support()
    main()
