# Discount Module

Calculates the final price of a shopping cart by applying discount campaigns.

- Python 3.9+ (tested on 3.12), **no third-party packages**
- Input: a JSON file (items + campaigns). Output: one JSON document on stdout.
- Rules: only one campaign per category, applied in the order **Coupon → On Top → Seasonal**.

## Run

```bash
# calculate one input file (run from the project root)
python -m discount.main examples/06_all_campaigns.json

# run the tests
python -m unittest -v        # pytest also works if you have it
```

Exit code is `0` on success and `1` on error. The output is always valid JSON.

## Input format

```json
{
  "items": [
    {"name": "T-Shirt", "price": 350, "category": "Clothing", "quantity": 2},
    {"name": "Hat",     "price": 250, "category": "Accessories"}
  ],
  "campaigns": [
    {"type": "fixed_amount", "amount": 50},
    {"type": "points", "points": 68},
    {"type": "seasonal", "every": 300, "discount": 40}
  ]
}
```

| Item field | Rule |
|---|---|
| `name` | non-empty string |
| `price` | price of **one** unit, >= 0, at most 2 decimal places |
| `category` | required; `Clothing`, `Accessories` or `Electronics` (case-insensitive) |
| `quantity` | whole number >= 1 (optional, default 1) |

| Campaign `type` | Category | Parameters |
|---|---|---|
| `fixed_amount` | Coupon | `amount` (>= 0) |
| `percentage` | Coupon | `percentage` (0–100) |
| `category_percentage` | On Top | `category`, `amount` (a **percentage**, 0–100) |
| `points` | On Top | `points` (whole number >= 0) |
| `seasonal` | Seasonal | `every` (> 0), `discount` (>= 0) |

`campaigns` may be empty or omitted. The order in the list does not matter, the module sorts them.

## Output

```json
{
  "status": "ok",
  "original_price": 1180.0,
  "applied": [
    {"type": "fixed_amount", "discount": 50.0},
    {"type": "points", "discount": 68.0},
    {"type": "seasonal", "discount": 120.0}
  ],
  "final_price": 942.0
}
```

On invalid input:

```json
{
  "status": "error",
  "error": {
    "code": "DUPLICATE_CATEGORY",
    "message": "only one coupon campaign is allowed ('fixed_amount' is already used, got 'percentage')",
    "field": "campaigns[1]"
  }
}
```

| `code` | Meaning |
|---|---|
| `INVALID_ITEM` | a problem in `items` |
| `INVALID_CAMPAIGN` | a bad campaign or bad parameter |
| `UNKNOWN_CAMPAIGN_TYPE` | `type` is missing or not supported |
| `DUPLICATE_CATEGORY` | two campaigns from the same category |
| `NUMBER_OUT_OF_RANGE` | a number is too large to calculate |
| `INVALID_JSON`, `INVALID_INPUT`, `FILE_NOT_FOUND`, `FILE_ERROR` | problems reading the input file |

`field` points to the exact place in the input, e.g. `items[1].price` or `campaigns[0].points`
(the index is the position in the **original** input, not the sorted order).

## Use as a library

```python
from discount.discount_calculator import calculate, DiscountError

result = calculate(items, campaigns)   # raises DiscountError subclasses on bad input
result["final_price"]                  # Decimal
result["applied"]                      # [{"type": "fixed_amount", "discount": Decimal("50.00")}, ...]
```

Only `calculate()` and the exceptions are public (`__all__`). Everything starting with `_` is private.

## Project layout

```
discount/
  discount_calculator.py   all the logic in one file (see sections below)
  main.py                  CLI: read JSON file, print JSON
tests/test_discount.py
examples/                  the assignment examples + a combined case + an invalid case
```

`discount_calculator.py` is read top to bottom:
exceptions (public) → constants → `_Item` → value helpers → campaigns (one function each) → parsing/validation → `calculate()` (public).

## How it works

1. Validate the items and the campaigns (types, parameters, one campaign per category).
2. Sort campaigns by category: Coupon, On Top, Seasonal.
3. Start from the cart total. For each campaign: `discount = min(round(campaign discount), current price)`, then `price -= discount`.
4. Return the original price, every discount that was applied and the final price.

**Adding a new campaign:** write one function and add one line to `_CAMPAIGNS` in `discount_calculator.py`.

## Design decisions

- **Decimal, not float.** Money with float gives errors like `0.1 + 0.2 != 0.3`. All discounts are rounded to 2 decimal places, half up.
- **One file, public vs private.** The module is small, so the logic stays in one file. Only `calculate()` and the exceptions are public; helpers start with `_` (Python's way of "private"). Tests only use the public API.
- **Plain functions + a registry (dict)** instead of a class per campaign. Each campaign is about 3 lines, and a dict lookup replaces a long `if/elif`.
- **Only one small data class (`_Item`).** It converts and validates an item once, so the rest of the code can use `item.price * item.quantity`. The result is a plain dict.
- **One place for rounding and capping** (`calculate()`), so no campaign can forget it and the final price can never be negative.
- **Custom exceptions with `code` / `message` / `field`.** Other programs can check `code`; people read `message`; `field` shows where to fix the input.
- **Validation first.** Bad input is rejected with a clear error instead of producing a wrong price.

## Assumptions

1. **Percentage discount by item category:** the parameter is called `amount`, as in the assignment, but its value is a **percentage** (0–100), as in the example (`"amount": 15` = 15% off).
2. **Each campaign works on the current price.** Points are capped at 20% of the price *after* the coupon, and Seasonal is calculated on the price *after* Coupon and On Top.
3. **Category discount after a coupon.** The category discount is calculated from the list price of the items in that category and subtracted from the current price (it is not reduced proportionally by the coupon). This keeps the logic simple; if a coupon and a category discount are combined the customer can receive slightly more than a strictly proportional split would give.
4. **Seasonal discount:** `floor(price / X) * Y`. Example: 830 THB, every 300, discount 40 → 2 × 40 = 80.
5. **Points:** 1 point = 1 THB, and the discount is `min(points, 20% of current price)`. The 20% is a fixed constant (`POINTS_CAP_RATE`). Cap is rounded down to the cent so it never exceeds 20%.
6. **Category groups:** Coupon = `fixed_amount`, `percentage`. On Top = `category_percentage`, `points`. Seasonal = `seasonal`. Using two campaigns from one group is an error (not silently ignored).
7. **A discount can't exceed the current price**, so the final price is never below 0.
8. Items have a `quantity` (default 1) and `price` is the unit price. Prices and money amounts have at most 2 decimal places. Numbers may be given as JSON numbers or numeric strings; booleans, `NaN` and `Infinity` are rejected.
9. **`category` is required on every item and must be `Clothing`, `Accessories` or `Electronics`.** These are the only categories the assignment names (in *Percentage discount by item category*), so any other value is rejected with `INVALID_ITEM` instead of being guessed. Only `category_percentage` uses the category today, but a real cart always carries the category of each item, so it is validated for every item. To support another category, add its name to `_ITEM_CATEGORIES`.
10. A `category_percentage` for a category that is not in the cart is **not** an error; the discount is simply 0.
11. Currency is THB; the module doesn't convert currencies.
