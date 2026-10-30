"""Discount calculator.

Public API (see __all__):
    calculate(items, campaigns)  -> dict with original_price, applied, final_price
    DiscountError and its subclasses (raised when the input is invalid)

Everything that starts with an underscore is private to this file.

Flow: validate -> sort campaigns (Coupon, On Top, Seasonal) -> apply one by one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, DecimalException, InvalidOperation
from typing import Any

__all__ = [
    "calculate",
    "DiscountError",
    "InvalidItemError",
    "InvalidCampaignError",
    "DuplicateCategoryError",
]


# ==========================================================================
# Exceptions (public: callers need to catch them)
# Every error has a machine-readable `code`, a human-readable `message`
# and a `field` that points at the part of the input that is wrong.
# ==========================================================================
class DiscountError(Exception):
    """Base class of every error raised by this module."""

    default_code = "DISCOUNT_ERROR"

    def __init__(self, message: str, field: str | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = code or self.default_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "error",
            "error": {"code": self.code, "message": self.message, "field": self.field},
        }


class InvalidItemError(DiscountError):
    default_code = "INVALID_ITEM"


class InvalidCampaignError(DiscountError):
    default_code = "INVALID_CAMPAIGN"


class DuplicateCategoryError(DiscountError):
    default_code = "DUPLICATE_CATEGORY"


# ==========================================================================
# Constants (private)
# ==========================================================================
_CENT = Decimal("0.01")
_POINTS_CAP_RATE = Decimal("0.20")  # points can pay at most 20% of the price

_ITEM_CATEGORIES = ("Clothing", "Accessories", "Electronics")

# Campaign categories. The number is the order of application.
_COUPON, _ON_TOP, _SEASONAL = 1, 2, 3
_GROUP_NAMES = {_COUPON: "coupon", _ON_TOP: "on top", _SEASONAL: "seasonal"}


# ==========================================================================
# Item (private): one cart line, already validated
# ==========================================================================
@dataclass(frozen=True)
class _Item:
    name: str
    price: Decimal  # price of ONE unit
    category: str
    quantity: int

    @property
    def total(self) -> Decimal:
        return self.price * self.quantity


# ==========================================================================
# Value helpers (private)
# ==========================================================================
def _money(value: Decimal) -> Decimal:
    """Round to 2 decimal places (half up)."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _to_decimal(value: Any, name: str, field: str, error_cls: type[DiscountError]) -> Decimal:
    """Convert a JSON value to a finite Decimal, or raise `error_cls`."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise error_cls(f"{name} must be a number", field)
    try:
        number = Decimal(str(value))  # str() avoids float noise (0.1 -> '0.1')
    except InvalidOperation:
        raise error_cls(f"{name} must be a number", field) from None
    if not number.is_finite():
        raise error_cls(f"{name} must be a finite number", field)
    return number


def _to_money(value: Any, name: str, field: str, error_cls: type[DiscountError]) -> Decimal:
    """Like _to_decimal, but also: not negative and at most 2 decimal places."""
    number = _to_decimal(value, name, field, error_cls)
    if number < 0:
        raise error_cls(f"{name} must not be negative", field)
    if number != _money(number):
        raise error_cls(f"{name} must have at most 2 decimal places", field)
    return number


def _to_category(value: Any, field: str, error_cls: type[DiscountError]) -> str:
    """Return the canonical category name, case-insensitive ('clothing' -> 'Clothing')."""
    if isinstance(value, str):
        for category in _ITEM_CATEGORIES:
            if value.strip().lower() == category.lower():
                return category
    raise error_cls(f"category must be one of: {', '.join(_ITEM_CATEGORIES)}", field)


# ==========================================================================
# Campaigns (private)
#
# Every campaign has the same signature:
#     campaign(price, items, params, field) -> Decimal   (the discount amount)
#   price   current price, after the discounts already applied
#   items   the cart items
#   params  parameters of the campaign from the input
#   field   path of the campaign in the input (for error messages)
# Rounding and capping the discount at the current price happen in calculate().
# ==========================================================================
def _money_param(params: dict, key: str, field: str, *, positive: bool = False) -> Decimal:
    where = f"{field}.{key}"
    value = _to_money(params.get(key), key, where, InvalidCampaignError)
    if positive and value == 0:
        raise InvalidCampaignError(f"{key} must be greater than 0", where)
    return value


def _percentage_param(params: dict, key: str, field: str) -> Decimal:
    where = f"{field}.{key}"
    value = _to_decimal(params.get(key), key, where, InvalidCampaignError)
    if not 0 <= value <= 100:
        raise InvalidCampaignError(f"{key} must be between 0 and 100", where)
    return value


def _fixed_amount(price: Decimal, items: list[_Item], params: dict, field: str) -> Decimal:
    """Coupon: subtract a fixed amount from the whole cart."""
    return _money_param(params, "amount", field)


def _percentage(price: Decimal, items: list[_Item], params: dict, field: str) -> Decimal:
    """Coupon: subtract a percentage of the whole cart."""
    rate = _percentage_param(params, "percentage", field)
    return price * rate / 100


def _category_percentage(price: Decimal, items: list[_Item], params: dict, field: str) -> Decimal:
    """On Top: percentage off all items of one category (based on their list price)."""
    category = _to_category(params.get("category"), f"{field}.category", InvalidCampaignError)
    rate = _percentage_param(params, "amount", field)  # "amount" is a percentage (0-100)
    category_total = sum((i.total for i in items if i.category == category), Decimal("0"))
    return category_total * rate / 100


def _points(price: Decimal, items: list[_Item], params: dict, field: str) -> Decimal:
    """On Top: 1 point = 1 THB, but at most 20% of the current price."""
    customer_points = params.get("points")
    if isinstance(customer_points, bool) or not isinstance(customer_points, int) or customer_points < 0:
        raise InvalidCampaignError("points must be a whole number >= 0", f"{field}.points")
    cap = (price * _POINTS_CAP_RATE).quantize(_CENT, rounding=ROUND_DOWN)
    return min(Decimal(customer_points), cap)


def _seasonal(price: Decimal, items: list[_Item], params: dict, field: str) -> Decimal:
    """Seasonal: for every X THB of the current price, subtract Y THB."""
    every = _money_param(params, "every", field, positive=True)
    discount = _money_param(params, "discount", field)
    return (price // every) * discount


# campaign type -> (campaign category, function)
# To add a campaign: write one function above and add one line here.
_CAMPAIGNS = {
    "fixed_amount": (_COUPON, _fixed_amount),
    "percentage": (_COUPON, _percentage),
    "category_percentage": (_ON_TOP, _category_percentage),
    "points": (_ON_TOP, _points),
    "seasonal": (_SEASONAL, _seasonal),
}


# ==========================================================================
# Input parsing / validation (private)
# ==========================================================================
def _parse_item(data: Any, index: int) -> _Item:
    where = f"items[{index}]"
    if not isinstance(data, dict):
        raise InvalidItemError("item must be an object", where)

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidItemError("name must be a non-empty string", f"{where}.name")

    price = _to_money(data.get("price"), "price", f"{where}.price", InvalidItemError)
    category = _to_category(data.get("category"), f"{where}.category", InvalidItemError)

    quantity = data.get("quantity", 1)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise InvalidItemError("quantity must be a whole number >= 1", f"{where}.quantity")

    return _Item(name.strip(), price, category, quantity)


def _parse_items(data: Any) -> list[_Item]:
    if not isinstance(data, list) or not data:
        raise InvalidItemError("items must be a non-empty list", "items")
    return [_parse_item(raw, index) for index, raw in enumerate(data)]


def _parse_campaigns(data: Any) -> list[tuple]:
    """Validate the campaigns and return them sorted: Coupon, On Top, Seasonal.

    Each result is a tuple: (input index, type, group, function, params).
    """
    if data is None:
        return []
    if not isinstance(data, list):
        raise InvalidCampaignError("campaigns must be a list", "campaigns")

    planned = []
    used_groups: dict[int, str] = {}  # group -> campaign type already used

    for index, raw in enumerate(data):
        where = f"campaigns[{index}]"
        if not isinstance(raw, dict):
            raise InvalidCampaignError("campaign must be an object", where)

        name = raw.get("type")
        if not isinstance(name, str) or name not in _CAMPAIGNS:
            raise InvalidCampaignError(
                f"unknown campaign type {name!r}; supported types: {', '.join(_CAMPAIGNS)}",
                f"{where}.type",
                code="UNKNOWN_CAMPAIGN_TYPE",
            )

        group, function = _CAMPAIGNS[name]
        if group in used_groups:
            raise DuplicateCategoryError(
                f"only one {_GROUP_NAMES[group]} campaign is allowed "
                f"('{used_groups[group]}' is already used, got '{name}')",
                where,
            )
        used_groups[group] = name

        params = {key: value for key, value in raw.items() if key != "type"}
        planned.append((index, name, group, function, params))

    planned.sort(key=lambda campaign: campaign[2])  # by group
    return planned


# ==========================================================================
# Public function
# ==========================================================================
def calculate(items_data: Any, campaigns_data: Any = None) -> dict[str, Any]:
    """Calculate the final price of a cart.

    items_data      list of {"name", "price", "category", "quantity" (optional)}
    campaigns_data  list of {"type": ..., <parameters>} (optional)

    Returns {"original_price": Decimal, "applied": [{"type", "discount"}], "final_price": Decimal}
    Raises a DiscountError subclass when the input is invalid.
    """
    try:
        items = _parse_items(items_data)
        campaigns = _parse_campaigns(campaigns_data)

        original_price = _money(sum((item.total for item in items), Decimal("0")))
        price = original_price
        applied = []

        for index, name, _group, function, params in campaigns:
            raw_discount = function(price, items, params, f"campaigns[{index}]")
            discount = min(_money(raw_discount), price)  # price never goes below 0
            price -= discount
            applied.append({"type": name, "discount": discount})

        return {"original_price": original_price, "applied": applied, "final_price": price}
    except DecimalException:
        # Safety net for absurdly large numbers that overflow Decimal.
        raise DiscountError("a number is outside the supported range", code="NUMBER_OUT_OF_RANGE") from None
