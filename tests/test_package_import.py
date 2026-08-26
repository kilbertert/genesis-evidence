import importlib
import pkgutil

import pytest

import genesis_evidence

PACKAGE_MODULES = {
    module.name
    for module in pkgutil.walk_packages(genesis_evidence.__path__, prefix="genesis_evidence.")
}

SENTINEL_MODULES = {
    "genesis_evidence.core.store",
    "genesis_evidence.literature.jats",
    "genesis_evidence.portal.api",
    "genesis_evidence.review.api",
}


def test_package_discovers_expected_modules() -> None:
    assert set(PACKAGE_MODULES) >= SENTINEL_MODULES


@pytest.mark.parametrize("module_name", sorted(PACKAGE_MODULES))
def test_package_module_imports(module_name: str) -> None:
    importlib.import_module(module_name)
