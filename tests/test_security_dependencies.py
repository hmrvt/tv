"""
Security regression tests — requirements.txt
Covers: CWE-1104 (unpinned/unreviewed third-party dependencies).

These tests treat requirements.txt as a security artefact.
They enforce structure (every package must be exactly pinned) and catch
accidental regression to unpinned or range-pinned specs.

Run with:  python -m unittest tests.test_security_dependencies -v
"""

import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REQ  = os.path.join(_ROOT, "requirements.txt")

# ---------------------------------------------------------------------------
# Parser — strips comments and blank lines, returns (name, spec) pairs.
# ---------------------------------------------------------------------------

_COMMENT_OR_BLANK = re.compile(r"^\s*(#.*)?$")
_REQUIREMENT_LINE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"\s*(?P<spec>[><=!~][^\s#]+)?"
    r"\s*(#.*)?$"
)


def _parse_requirements(path: str) -> list[tuple[str, str]]:
    """Return [(normalised_name, version_spec), ...] for every non-comment line."""
    results = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if _COMMENT_OR_BLANK.match(line):
                continue
            m = _REQUIREMENT_LINE.match(line)
            if not m:
                raise ValueError(f"Could not parse requirements line: {line!r}")
            name = m.group("name").lower().replace("-", "_")
            spec = (m.group("spec") or "").strip()
            results.append((name, spec))
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRequirementsFileExists(unittest.TestCase):

    def test_requirements_txt_present(self):
        self.assertTrue(
            os.path.isfile(_REQ),
            f"requirements.txt not found at {_REQ}",
        )

    def test_requirements_txt_not_empty(self):
        reqs = _parse_requirements(_REQ)
        self.assertGreater(len(reqs), 0, "requirements.txt has no packages")


class TestAllPackagesPinned(unittest.TestCase):
    """Every line must use == so the exact version is reproducible (CWE-1104)."""

    def test_no_unpinned_packages(self):
        unpinned = []
        for name, spec in _parse_requirements(_REQ):
            if not spec:
                unpinned.append(name)
        self.assertEqual(
            unpinned, [],
            f"Unpinned packages (add ==<version>): {unpinned}",
        )

    def test_no_range_pinned_packages(self):
        """>=, <=, ~=, != all allow future versions with unknown security posture."""
        range_ops = (">=", "<=", "~=", "!=", ">", "<")
        bad = []
        for name, spec in _parse_requirements(_REQ):
            if any(spec.startswith(op) for op in range_ops):
                bad.append(f"{name}{spec}")
        self.assertEqual(
            bad, [],
            f"Range-pinned packages found (use == instead): {bad}",
        )

    def test_all_packages_use_exact_pin(self):
        not_exact = []
        for name, spec in _parse_requirements(_REQ):
            if not spec.startswith("=="):
                not_exact.append(f"{name}{spec!r}")
        self.assertEqual(
            not_exact, [],
            f"Packages without exact == pin: {not_exact}",
        )


class TestExpectedPackagesPresent(unittest.TestCase):
    """Guard against accidental removal of a required package."""

    _REQUIRED = {"python_vlc", "yt_dlp", "pillow"}

    def test_all_required_packages_listed(self):
        listed = {name for name, _ in _parse_requirements(_REQ)}
        missing = self._REQUIRED - listed
        self.assertEqual(
            missing, set(),
            f"Required packages missing from requirements.txt: {missing}",
        )


class TestInstalledVersionsMatchPins(unittest.TestCase):
    """
    Verify that what is actually installed matches the pinned spec.
    Fails loudly if someone ran `pip install --upgrade` without updating the pin,
    or if the CI environment has a different version installed.
    """

    def _installed_version(self, package: str) -> str | None:
        """Return the installed version string, or None if not installed."""
        import importlib.metadata
        try:
            return importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            return None

    def test_installed_versions_match_pins(self):
        mismatches = []
        for name, spec in _parse_requirements(_REQ):
            if not spec.startswith("=="):
                continue
            pinned_version = spec[2:]
            # importlib.metadata uses the canonical pip package name
            canonical = name.replace("_", "-")
            installed = self._installed_version(canonical)
            if installed is None:
                # Not installed — skip (another test covers presence)
                continue
            if installed != pinned_version:
                mismatches.append(
                    f"{canonical}: pinned={pinned_version}, installed={installed}"
                )
        self.assertEqual(
            mismatches, [],
            f"Installed versions differ from pins:\n" + "\n".join(mismatches),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
