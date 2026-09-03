"""Runtime-only diagnostics for the swallowed post-fixture native observation error."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from modlab.validation import mo2_containment_service as service


def _diagnostic_wrapper(name, function):
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BaseException as error:
            print(
                f"TASK7A-DIAG {name} args={args!r} error={type(error).__name__}: {error}",
                file=sys.stderr,
                flush=True,
            )
            raise
    return invoke


service.pin_stable_direct_object = _diagnostic_wrapper(
    "pin_stable_direct_object", service.pin_stable_direct_object
)
service.read_pinned_file = _diagnostic_wrapper(
    "read_pinned_file", service.read_pinned_file
)
service.os.scandir = _diagnostic_wrapper("os.scandir", service.os.scandir)
_original_observation = service._mutation_root_observation


def _observe(*args, **kwargs):
    result = _original_observation(*args, **kwargs)
    if result is None:
        print(
            f"TASK7A-DIAG observation-returned-None root={Path(args[0])!s} kwargs={kwargs!r}",
            file=sys.stderr,
            flush=True,
        )
    return result


service._mutation_root_observation = _observe
suite = unittest.defaultTestLoader.loadTestsFromName(
    "tests.test_mo2_preparation_recovery.RealPreparationRestartTests."
    "test_foreground_failed_attempt_recovers_once_into_completely_fresh_real_preparation"
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
