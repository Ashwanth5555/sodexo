"""Training entry point for the built-in series.

There are none. Every series comes from a file the client uploaded, so the
real runner is datasets/training.py; this module survives only because the
/api/models/train route is still wired to it and an older frontend build may
still call it.

It reports the situation rather than silently creating an empty job, so
"Train All Models" cannot look like it did something.
"""
import threading


_lock = threading.Lock()


def is_training_active() -> bool:
    """Never active: this runner has nothing to train."""
    return False


def start_training_job(window_days: int | None = None) -> str:
    raise RuntimeError(
        "There are no built-in series to train. Upload a dataset on the Data Quality page "
        "and train it from there."
    )
