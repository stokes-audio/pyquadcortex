"""The package version, in one place.

Both namespaces publish it (``pyquadcortex.__version__`` and
``pyquadcortex.protocol.__version__``), and ``pyproject.toml`` reads it from
here, so it lives in its own module rather than in either package's
``__init__``, where one would have to import the other to get it.
"""

__version__ = "0.40.0"
