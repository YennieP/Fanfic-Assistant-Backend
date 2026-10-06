from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parent.parent


def requirement_map(requirements: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in requirements:
        requirement = Requirement(value)
        name = canonicalize_name(requirement.name)
        if name in parsed:
            raise ValueError(f"duplicate dependency: {name}")
        parsed[name] = str(requirement.specifier)
    return parsed


def read_requirements(path: Path) -> dict[str, str]:
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return requirement_map(lines)


def assert_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise AssertionError(f"{label} mismatch:\nactual: {actual!r}\nexpected: {expected!r}")


def main() -> int:
    python_version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert_equal(".python-version", python_version, "3.12")

    with (ROOT / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)
    assert_equal("requires-python", pyproject["project"]["requires-python"], ">=3.12,<3.13")

    project_dependencies = requirement_map(pyproject["project"]["dependencies"])
    production_requirements = read_requirements(ROOT / "requirements.txt")
    assert_equal("production direct dependencies", production_requirements, project_dependencies)

    project_dev_dependencies = requirement_map(pyproject["dependency-groups"]["dev"])
    development_requirements = read_requirements(ROOT / "requirements-dev.txt")
    assert_equal("development direct dependencies", development_requirements, project_dev_dependencies)

    with (ROOT / "uv.lock").open("rb") as file:
        uv_lock = tomllib.load(file)
    assert_equal("uv.lock requires-python", uv_lock["requires-python"], "==3.12.*")

    root_package = next(
        package
        for package in uv_lock["package"]
        if package["name"] == pyproject["project"]["name"]
    )
    locked_dependencies = {
        canonicalize_name(item["name"]): item.get("specifier", "")
        for item in root_package["metadata"]["requires-dist"]
    }
    assert_equal("uv.lock production dependencies", locked_dependencies, project_dependencies)

    locked_dev_dependencies = {
        canonicalize_name(item["name"]): item.get("specifier", "")
        for item in root_package["metadata"]["requires-dev"]["dev"]
    }
    assert_equal("uv.lock development dependencies", locked_dev_dependencies, project_dev_dependencies)

    print("Environment contract checks passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, StopIteration, ValueError) as exc:
        print(f"Environment contract check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
