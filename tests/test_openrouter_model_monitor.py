import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.check_openrouter_model import CatalogCheckError, validate_catalog


MODEL_ID = "google/gemma-4-31b-it:free"
ROOT = Path(__file__).resolve().parent.parent


def _payload(*, prompt="0", completion="0", parameters=None):
    return {
        "data": [{
            "id": MODEL_ID,
            "pricing": {
                "prompt": prompt,
                "completion": completion,
            },
            "supported_parameters": parameters or ["response_format"],
        }]
    }


def test_validate_catalog_accepts_configured_free_json_model():
    model = validate_catalog(_payload(), MODEL_ID)

    assert model["id"] == MODEL_ID


def test_validate_catalog_rejects_absent_configured_model():
    with pytest.raises(CatalogCheckError, match="configured model is absent"):
        validate_catalog({"data": []}, MODEL_ID)


@pytest.mark.parametrize("field", ["prompt", "completion"])
def test_validate_catalog_rejects_nonzero_text_price(field):
    prices = {"prompt": "0", "completion": "0"}
    prices[field] = "0.000001"

    with pytest.raises(CatalogCheckError, match=rf"pricing\.{field} is no longer zero"):
        validate_catalog(_payload(**prices), MODEL_ID)


def test_validate_catalog_rejects_missing_response_format_support():
    with pytest.raises(CatalogCheckError, match="no longer supports response_format"):
        validate_catalog(_payload(parameters=["temperature"]), MODEL_ID)


def test_validate_catalog_rejects_malformed_response():
    with pytest.raises(CatalogCheckError, match="does not contain a data list"):
        validate_catalog({"data": {}}, MODEL_ID)


def test_monitor_runs_without_project_dependencies(tmp_path):
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(_payload()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(ROOT / "scripts" / "check_openrouter_model.py"),
            "--catalog-file",
            str(catalog_file),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f"OpenRouter model check passed: {MODEL_ID}" in result.stdout
