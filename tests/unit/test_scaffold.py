"""Scaffold smoke test - proves the nova package is installed and importable."""

import nova


def test_nova_package_importable() -> None:
    assert nova.__version__ == "0.1.0"
