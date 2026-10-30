"""Command line entry point.

    python -m discount.main examples/01_fixed_amount.json

Reads a JSON file, prints ONE JSON document to stdout.
Exit code: 0 = ok, 1 = error (the JSON then has "status": "error").
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from discount.discount_calculator import DiscountError, calculate


def load_payload(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as file:
            payload = json.load(file)
    except FileNotFoundError:
        raise DiscountError(f"file not found: {path}", code="FILE_NOT_FOUND") from None
    except json.JSONDecodeError as error:
        raise DiscountError(f"invalid JSON: {error}", code="INVALID_JSON") from None
    except (OSError, UnicodeDecodeError) as error:
        raise DiscountError(f"cannot read file: {error}", code="FILE_ERROR") from None

    if not isinstance(payload, dict):
        raise DiscountError(
            "top-level JSON must be an object with 'items' and 'campaigns'",
            code="INVALID_INPUT",
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calculate a cart price with discount campaigns.")
    parser.add_argument("input", help="path to the input JSON file")
    args = parser.parse_args(argv)

    try:
        payload = load_payload(args.input)
        result = calculate(payload.get("items"), payload.get("campaigns"))
        output = {"status": "ok", **result}
        exit_code = 0
    except DiscountError as error:
        output = error.to_dict()
        exit_code = 1

    print(json.dumps(output, indent=2, default=float))  # default=float: Decimal -> number
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
