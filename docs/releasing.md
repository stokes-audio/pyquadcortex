# Releasing

A release is irreversible: **PyPI does not allow re-uploading a version**, and a
filename can never be reused, even if the release is deleted. A stale or wrong
artifact is therefore permanent until the next version. Work through this list in
order, every time.

## 1. The tree must be exactly what you intend to publish

```bash
git status --porcelain        # must be empty
git log -1 --oneline          # note this commit; it is what you are shipping
git push                      # the published artifact should exist in history
```

An artifact built from an uncommitted or unpushed tree cannot be traced back to
source later, and nobody can reproduce it.

## 2. Bump the version if the last one was published

The version lives in **one** place, `pyquadcortex/__init__.py`, and
`pyproject.toml` reads it. Publishing the same version twice is impossible, so
decide now rather than at the upload prompt.

## 3. Build FRESH, from a clean dist/

```bash
rm -rf dist build
.venv/bin/python -m build
.venv/bin/twine check dist/*
```

Deleting `dist/` first is not optional. A stale wheel left over from an earlier
build is the easiest way to publish the wrong thing - `twine upload dist/*`
uploads whatever is sitting there, not whatever your working tree says.

## 4. Verify the artifact, not the source tree

The long description on the project page comes from `README.md` **as it was when
the artifact was built**. Check the built artifact rather than assuming:

```bash
python - <<'PY'
import zipfile
m = zipfile.ZipFile("dist/pyquadcortex-<version>-py3-none-any.whl") \
        .read("pyquadcortex-<version>.dist-info/METADATA").decode()
for line in m.splitlines():
    if line.startswith(("Name:", "Version:", "License", "Requires-", "Author-email:")):
        print(line)
print("readme chars:", len(m.split("\n\n", 1)[1]))
PY
```

Then install it into a throwaway environment and exercise it - import the package,
run `qcctl --help`, and confirm the pinned `protobuf` resolves:

```bash
uv venv --python 3.11 /tmp/relcheck
uv pip install --python /tmp/relcheck/bin/python dist/*.whl
cd /tmp && /tmp/relcheck/bin/python -c "import pyquadcortex; print(pyquadcortex.__version__)"
/tmp/relcheck/bin/qcctl --help
```

Run it from a directory that is **not** the repo, or Python will import the source
tree instead of the installed package and prove nothing.

## 5. Rehearse on TestPyPI

```bash
.venv/bin/twine upload --repository-url https://test.pypi.org/legacy/ dist/*
```

Username `__token__`, password a TestPyPI API token. TestPyPI is a separate system
from PyPI: separate account, separate token, separate namespace.

The tokens live in `.env` (gitignored): `TEST_PYPI_TOKEN` for TestPyPI, `PYPI_TOKEN`
for the real index. The names are deliberately distinct so a rehearsal and a real
publish cannot pick up each other's credential - load the right one into
`TWINE_PASSWORD` and never echo either.

Then install from it, remembering that dependencies live on real PyPI:

```bash
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ pyquadcortex
```

Finally, **look at the rendered project page**, not just the install. The readme
and the project links are the part that cannot be tested locally.

## 6. Publish

```bash
.venv/bin/twine upload dist/*
```

Then tag the commit you shipped, so the artifact is traceable:

```bash
git tag -a v<version> -m "v<version>" && git push --tags
```

## Notes

- CI builds and runs `twine check` on every push, so a red build means the
  artifact is broken before you ever reach a release.
- The generated `*_pb2.py` bindings are committed, so a release needs no protoc.
  If they were regenerated with a newer `protobuf`, the runtime pin in
  `pyproject.toml` must move with them or every install breaks.
