"""The device profile seam (ADR-0020, spec 2026-09-03)."""
import importlib

from pyquadcortex.protocol import models, options, params
from pyquadcortex.protocol.catalogs import coros_4_0_1


def test_the_baseline_shims_re_export_the_4_0_1_snapshot():
    """`protocol.models` keeps meaning CorOS 4.0.1 until an ADR moves it."""
    assert models.ALL is coros_4_0_1.models.ALL
    assert params.BY_MODEL is coros_4_0_1.params.BY_MODEL
    assert options.OPTION_LABELS is coros_4_0_1.options.OPTION_LABELS
    # Only `options.py` defines `__all__` (`models.py` and `params.py` do not);
    # this checks re-export fidelity on the module that actually has one.
    assert set(options.__all__) == set(coros_4_0_1.options.__all__)


def test_the_old_import_paths_still_resolve_as_modules():
    for name in ("models", "params", "options"):
        importlib.import_module(f"pyquadcortex.protocol.{name}")
        importlib.import_module(f"pyquadcortex.protocol.catalogs.coros_4_0_1.{name}")
