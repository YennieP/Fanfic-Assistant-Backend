from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from generation.providers.catalog import get_provider_definition  # noqa: E402


CATALOG_URL = "https://openrouter.ai/api/v1/models"


class CatalogCheckError(RuntimeError):
    pass


def _require_zero_price(pricing: object, field: str) -> None:
    if not isinstance(pricing, dict) or field not in pricing:
        raise CatalogCheckError(f"pricing.{field} is missing")
    try:
        price = Decimal(str(pricing[field]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CatalogCheckError(f"pricing.{field} is not numeric") from exc
    if price != 0:
        raise CatalogCheckError(f"pricing.{field} is no longer zero")


def validate_catalog(payload: object, model_id: str) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise CatalogCheckError("catalog response does not contain a data list")

    model = next(
        (
            item
            for item in payload["data"]
            if isinstance(item, dict) and item.get("id") == model_id
        ),
        None,
    )
    if model is None:
        raise CatalogCheckError(f"configured model is absent: {model_id}")

    _require_zero_price(model.get("pricing"), "prompt")
    _require_zero_price(model.get("pricing"), "completion")

    supported = model.get("supported_parameters")
    if not isinstance(supported, list) or "response_format" not in supported:
        raise CatalogCheckError("configured model no longer supports response_format")

    return model


def fetch_catalog(url: str = CATALOG_URL) -> object:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Fanfic-Assistant-model-monitor/1.0",
        },
    )
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> int:
    definition = get_provider_definition("openrouter")
    if definition is None:
        print("OpenRouter model check failed: catalog definition is missing", file=sys.stderr)
        return 1

    try:
        model = validate_catalog(fetch_catalog(), definition.model)
    except (CatalogCheckError, json.JSONDecodeError, OSError, URLError) as exc:
        print(f"OpenRouter model check failed: {exc}", file=sys.stderr)
        return 1

    print(
        "OpenRouter model check passed: "
        f"{model['id']} exists, remains free for text, and supports response_format"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
