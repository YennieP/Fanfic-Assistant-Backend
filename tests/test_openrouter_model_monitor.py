import pytest

from scripts.check_openrouter_model import CatalogCheckError, validate_catalog


MODEL_ID = "google/gemma-4-31b-it:free"


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
