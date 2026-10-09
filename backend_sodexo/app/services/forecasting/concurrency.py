"""Shared plumbing for running a training job's series in parallel.

Both training entry points - the built-in Sodexo series and an uploaded
dataset - fit a list of independent series and report progress against one
TrainingJob row. The parallelism and the progress bookkeeping are identical;
only the per-series work differs. This module holds the common half so the
two runners cannot drift apart in how they count steps or handle a worker
that raises.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, Sequence, TypeVar

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import TrainingJob

logger = logging.getLogger(__name__)

T = TypeVar("T")


def available_cpus() -> int:
    """Cores this process may actually use, not cores the machine has.

    os.cpu_count() reports the HOST's cores from inside a container, so on
    Azure Container Apps (2 vCPU) it happily returns 16 and we would start 16
    workers against a 2-core quota - slower than running serially, because
    every fitter also spawns its own BLAS threads. The cgroup v2 quota is the
    number that binds, so prefer it, then affinity, then the raw count.
    """
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="utf-8") as handle:
            quota_raw, period_raw = handle.read().split()
        if quota_raw != "max":
            quota = int(quota_raw) / int(period_raw)
            if quota >= 1:
                return int(quota)
            # A sub-core quota still gets one worker; fractions cannot be
            # split across threads, and 0 workers would deadlock the pool.
            return 1
    except (OSError, ValueError):
        # Not Linux, no cgroup v2, or an unreadable/malformed file. Fall
        # through - this is an optimisation, never a correctness requirement.
        pass

    sched_getaffinity = getattr(os, "sched_getaffinity", None)
    if sched_getaffinity is not None:
        try:
            return max(1, len(sched_getaffinity(0)))
        except OSError:
            pass

    return max(1, os.cpu_count() or 1)


# Rough working set for one series being fitted: its history and future
# frames, the per-fold copies, and whatever the fitter allocates on top.
# TensorFlow and the BLAS libraries are loaded once per process and shared
# across the threads, so they are not counted per worker - but they do take a
# large fixed bite, which is what MEMORY_HEADROOM_MB reserves.
MEMORY_PER_WORKER_MB = 350
MEMORY_HEADROOM_MB = 1200


def memory_limited_workers() -> int | None:
    """Workers the container's memory limit can support, or None if unknown.

    Parallel fitting multiplies the live DataFrames, and a 40-series upload
    on a 4Gi container will OOM long before it runs out of CPU. An OOM kill
    loses the whole job with no error in the UI, which is a far worse
    outcome than a slower run, so memory gets a veto over the CPU count.
    """
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path, encoding="utf-8") as handle:
                raw = handle.read().strip()
        except OSError:
            continue
        if raw in ("max", ""):
            return None
        try:
            limit_mb = int(raw) / (1024 * 1024)
        except ValueError:
            continue
        # Absurdly large values mean "unlimited" on cgroup v1.
        if limit_mb > 1024 * 1024:
            return None
        usable = limit_mb - MEMORY_HEADROOM_MB
        if usable <= 0:
            return 1
        return max(1, int(usable // MEMORY_PER_WORKER_MB))
    return None


def resolve_worker_count(task_count: int) -> int:
    """How many series to fit at once.

    Never more workers than tasks, never more than usable cores, and never
    more than the memory limit can hold. Every fitter already spawns its own
    BLAS/TensorFlow threads, so oversubscribing CPU turns parallelism into
    cache thrash; oversubscribing memory gets the container OOM-killed.

    training_max_workers overrides the lot, for a host where neither the
    cgroup quota nor affinity tells the truth.
    """
    if task_count <= 0:
        return 1
    configured = get_settings().training_max_workers
    if configured and configured > 0:
        return max(1, min(configured, task_count))

    limit = min(task_count, available_cpus())
    by_memory = memory_limited_workers()
    if by_memory is not None:
        limit = min(limit, by_memory)
    return max(1, limit)


def resolve_skipped_models(series_count: int) -> frozenset[str]:
    """Which model ids this run should not fit.

    Returns an empty set for the full portfolio. The "auto" profile exists
    because the right answer differs by situation: four built-in series can
    afford the slow models, while a client's 40-group upload multiplies them
    into a wait nobody will sit through.
    """
    from app.services.forecasting.contract import SLOW_MODEL_IDS

    profile = (get_settings().training_profile or "auto").strip().lower()
    if profile == "full":
        return frozenset()
    if profile == "fast":
        return SLOW_MODEL_IDS
    if profile != "auto":
        logger.warning("Unknown TRAINING_PROFILE %r - treating it as 'auto'.", profile)
    threshold = get_settings().training_fast_mode_threshold
    return SLOW_MODEL_IDS if series_count > threshold else frozenset()


class ProgressReporter:
    """Serialises progress writes coming from worker threads.

    Each update opens its own short-lived session: a Session is not thread
    safe, and sharing one across workers corrupts its identity map even when
    the writes themselves are serialised by a lock.
    """

    def __init__(self, job_id: str):
        self.job_id = job_id
        self._lock = threading.Lock()
        self._completed = 0

    @property
    def completed(self) -> int:
        with self._lock:
            return self._completed

    def step(self, model_label: str, series_label: str) -> None:
        self.advance(1, model_label, series_label)

    def starting(self, model_label: str, series_label: str) -> None:
        """Name the model about to be fitted, without counting it.

        The count only moves when a model finishes, and the first one on an
        hourly series can take minutes - so a run showed "0 of 52" and a blank
        label for long enough to look hung. Naming the work in flight costs one
        small write per model and makes the difference between slow and stuck.
        """
        self.advance(0, model_label, series_label)

    def advance(self, count: int, model_label: str | None, series_label: str | None) -> None:
        """Record `count` finished model runs.

        count > 1 covers the case where a whole series is written off at once
        because its frame could not be built - every model in it is marked
        failed together, and the progress bar has to account for all of them
        or the job finishes reading n/52 with n short of the total.
        """
        with self._lock:
            self._completed += count
            completed = self._completed
            db = SessionLocal()
            try:
                job = db.get(TrainingJob, self.job_id)
                if job is None:
                    return
                job.completed_steps = completed
                # With parallel series these name whichever model finished
                # most recently, not an ordered march through the portfolio.
                # The count is the honest progress signal.
                if series_label is not None:
                    job.current_series = series_label[:60]
                if model_label is not None:
                    job.current_model = model_label
                db.commit()
            except Exception:
                db.rollback()
                # A failed progress write must never kill a run that is
                # otherwise fine - the count catches up on the next step.
                logger.warning("Could not record training progress", exc_info=True)
            finally:
                db.close()


@contextmanager
def _limited_blas_threads(workers: int):
    """Stop the worker threads fighting each other for cores.

    numpy's BLAS spawns one thread per core for a single fit. Run four series
    at once on a sixteen-core machine and that is sixty-four threads competing
    for sixteen cores - the fits do not go four times faster, they go slower,
    because most of the time goes on context switching. Measured on an hourly
    four-series file, the run sat at 0 of 52 for over ten minutes.

    We already parallelise across series, which is the coarser and more
    effective split, so each worker gets its own fair share of cores and no
    more. Silently skipped if threadpoolctl is unavailable - a slow run beats
    a run that will not start.
    """
    share = max(1, available_cpus() // max(1, workers))
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        logger.info("threadpoolctl unavailable - BLAS threads left at their default.")
        yield
        return
    logger.info("Limiting each of %d worker(s) to %d BLAS thread(s)", workers, share)
    with threadpool_limits(limits=share):
        yield


def run_series_in_parallel(
    items: Sequence[T],
    work: Callable[[T], None],
    describe: Callable[[T], str],
    on_failure: Callable[[T, Exception], None] | None = None,
) -> list[str]:
    """Run `work` over every item concurrently; return the failure messages.

    A worker that raises does not cancel its siblings: their model runs are
    already committed and are perfectly usable. The caller decides what a
    partial failure means for the job's final status.
    """
    workers = resolve_worker_count(len(items))
    failures: list[str] = []
    if not items:
        return failures

    logger.info("Fitting %d series across %d worker(s)", len(items), workers)
    with _limited_blas_threads(workers), ThreadPoolExecutor(max_workers=workers, thread_name_prefix="train") as pool:
        futures = {pool.submit(work, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                future.result()
            except Exception as exc:
                logger.exception("Series %s failed during training", describe(item))
                failures.append(f"{describe(item)}: {exc}")
                if on_failure is not None:
                    try:
                        on_failure(item, exc)
                    except Exception:
                        logger.exception("Failure handler itself failed for %s", describe(item))
    return failures


def finalise_job(job_id: str, failures: Iterable[str], total_units: int) -> None:
    """Close out a training job.

    Partial success is still success for the series that completed, so the
    status reflects whether anything got through rather than whether
    everything did.
    """
    failure_list = list(failures)
    db = SessionLocal()
    try:
        job = db.get(TrainingJob, job_id)
        if job is None:
            return
        job.status = "FAILED" if total_units and len(failure_list) >= total_units else "COMPLETED"
        job.error_message = (" | ".join(failure_list))[:990] if failure_list else None
        from datetime import datetime

        job.completed_at = datetime.utcnow()
        job.current_series = None
        job.current_model = None
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not finalise training job %s", job_id)
    finally:
        db.close()
