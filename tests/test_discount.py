"""Tests for the discount module.

Run with:  python -m unittest -v      (no extra packages needed)
pytest also works:  pytest
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path

from discount.discount_calculator import (
    DiscountError,
    DuplicateCategoryError,
    InvalidCampaignError,
    InvalidItemError,
    calculate,
)
from discount.main import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def item(name="T-Shirt", price=350, category="Clothing", **extra):
    return {"name": name, "price": price, "category": category, **extra}


# The cart used by several examples in the assignment: 350 + 250 + 230 = 830
CART_830 = [
    item("T-Shirt", 350, "Clothing"),
    item("Hat", 250, "Accessories"),
    item("Belt", 230, "Accessories"),
]
# 350 + 250 = 600
CART_600 = [item("T-Shirt", 350, "Clothing"), item("Hat", 250, "Accessories")]


def final(items, campaigns=None):
    return calculate(items, campaigns)["final_price"]


class AssignmentExamples(unittest.TestCase):
    """The five examples written in the assignment."""

    def test_fixed_amount(self):
        self.assertEqual(final(CART_600, [{"type": "fixed_amount", "amount": 50}]), Decimal("550"))

    def test_percentage(self):
        self.assertEqual(final(CART_600, [{"type": "percentage", "percentage": 10}]), Decimal("540"))

    def test_category_percentage(self):
        cart = [
            item("T-Shirt", 350, "Clothing"),
            item("Hoodie", 700, "Clothing"),
            item("Watch", 850, "Electronics"),
            item("Bag", 640, "Accessories"),
        ]
        campaign = {"type": "category_percentage", "category": "Clothing", "amount": 15}
        self.assertEqual(final(cart, [campaign]), Decimal("2382.5"))

    def test_points(self):
        self.assertEqual(final(CART_830, [{"type": "points", "points": 68}]), Decimal("762"))

    def test_seasonal(self):
        campaign = {"type": "seasonal", "every": 300, "discount": 40}
        self.assertEqual(final(CART_830, [campaign]), Decimal("750"))


class CampaignEdgeCases(unittest.TestCase):
    def test_no_campaigns_means_no_discount(self):
        for campaigns in (None, []):
            result = calculate(CART_600, campaigns)
            self.assertEqual(result["final_price"], Decimal("600"))
            self.assertEqual(result["applied"], [])

    def test_quantity_is_multiplied(self):
        self.assertEqual(final([item(price=100, quantity=3)]), Decimal("300"))

    def test_fixed_amount_larger_than_cart_gives_zero_not_negative(self):
        self.assertEqual(final(CART_600, [{"type": "fixed_amount", "amount": 9999}]), Decimal("0"))

    def test_percentage_bounds(self):
        self.assertEqual(final(CART_600, [{"type": "percentage", "percentage": 0}]), Decimal("600"))
        self.assertEqual(final(CART_600, [{"type": "percentage", "percentage": 100}]), Decimal("0"))

    def test_percentage_rounds_half_up_to_2_decimals(self):
        # 10% of 99.99 = 9.999 -> 10.00
        result = calculate([item(price="99.99")], [{"type": "percentage", "percentage": 10}])
        self.assertEqual(result["applied"][0]["discount"], Decimal("10.00"))
        self.assertEqual(result["final_price"], Decimal("89.99"))

    def test_points_are_capped_at_20_percent(self):
        result = calculate([item(price=100)], [{"type": "points", "points": 500}])
        self.assertEqual(result["applied"][0]["discount"], Decimal("20.00"))
        self.assertEqual(result["final_price"], Decimal("80.00"))

    def test_points_zero(self):
        self.assertEqual(final(CART_600, [{"type": "points", "points": 0}]), Decimal("600"))

    def test_seasonal_below_threshold_gives_no_discount(self):
        self.assertEqual(final([item(price=299)], [{"type": "seasonal", "every": 300, "discount": 40}]), Decimal("299"))

    def test_seasonal_uses_whole_multiples_only(self):
        # 650 -> 2 full blocks of 300 -> 80 off
        self.assertEqual(final([item(price=650)], [{"type": "seasonal", "every": 300, "discount": 40}]), Decimal("570"))

    def test_category_without_matching_items_gives_no_discount(self):
        campaign = {"type": "category_percentage", "category": "Electronics", "amount": 50}
        self.assertEqual(final(CART_600, [campaign]), Decimal("600"))

    def test_category_name_is_case_insensitive(self):
        campaign = {"type": "category_percentage", "category": "clothing", "amount": 10}
        self.assertEqual(final(CART_600, [campaign]), Decimal("565"))

    def test_decimal_precision_has_no_float_error(self):
        result = calculate([item(price="0.1"), item("B", "0.2")])
        self.assertEqual(result["original_price"], Decimal("0.30"))

    def test_discounts_never_push_price_below_zero(self):
        campaigns = [
            {"type": "fixed_amount", "amount": 600},
            {"type": "category_percentage", "category": "Clothing", "amount": 100},
            {"type": "seasonal", "every": 1, "discount": 100},
        ]
        result = calculate(CART_600, campaigns)
        self.assertEqual(result["final_price"], Decimal("0"))


class ApplyingMultipleCampaigns(unittest.TestCase):
    def test_all_three_groups(self):
        # 830 -> coupon -50 = 780 -> points -68 = 712 -> seasonal 2*40 = -80 = 632
        campaigns = [
            {"type": "fixed_amount", "amount": 50},
            {"type": "points", "points": 68},
            {"type": "seasonal", "every": 300, "discount": 40},
        ]
        result = calculate(CART_830, campaigns)
        self.assertEqual(result["final_price"], Decimal("632"))
        self.assertEqual([a["discount"] for a in result["applied"]], [Decimal(50), Decimal(68), Decimal(80)])

    def test_order_follows_coupon_then_on_top_then_seasonal_not_input_order(self):
        in_order = [
            {"type": "fixed_amount", "amount": 50},
            {"type": "points", "points": 68},
            {"type": "seasonal", "every": 300, "discount": 40},
        ]
        reversed_input = list(reversed(in_order))
        a, b = calculate(CART_830, in_order), calculate(CART_830, reversed_input)
        self.assertEqual(a["final_price"], b["final_price"])
        self.assertEqual([x["type"] for x in b["applied"]], ["fixed_amount", "points", "seasonal"])

    def test_order_matters_seasonal_uses_price_after_coupon(self):
        # 600 -> 10% = 540 -> every 300 off 40 = 1 block -> 500
        campaigns = [
            {"type": "percentage", "percentage": 10},
            {"type": "seasonal", "every": 300, "discount": 40},
        ]
        self.assertEqual(final(CART_600, campaigns), Decimal("500"))

    def test_points_cap_uses_price_after_coupon(self):
        # 600 - 300 = 300 -> cap 20% = 60 even though 500 points are offered
        campaigns = [{"type": "fixed_amount", "amount": 300}, {"type": "points", "points": 500}]
        self.assertEqual(final(CART_600, campaigns), Decimal("240"))

    def test_one_from_each_on_top_group_is_rejected_when_both_on_top(self):
        campaigns = [
            {"type": "category_percentage", "category": "Clothing", "amount": 10},
            {"type": "points", "points": 10},
        ]
        with self.assertRaises(DuplicateCategoryError):
            calculate(CART_600, campaigns)

    def test_input_is_not_modified(self):
        items = [item()]
        campaigns = [{"type": "fixed_amount", "amount": 50}]
        calculate(items, campaigns)
        self.assertEqual(items, [item()])
        self.assertEqual(campaigns, [{"type": "fixed_amount", "amount": 50}])


class ItemValidation(unittest.TestCase):
    def assert_item_error(self, items, field=None):
        with self.assertRaises(InvalidItemError) as ctx:
            calculate(items, [])
        self.assertEqual(ctx.exception.code, "INVALID_ITEM")
        if field:
            self.assertEqual(ctx.exception.field, field)

    def test_items_must_be_a_non_empty_list(self):
        for bad in (None, [], {}, "items", 5):
            self.assert_item_error(bad, "items")

    def test_item_must_be_an_object(self):
        self.assert_item_error(["T-Shirt"], "items[0]")

    def test_name_is_required(self):
        self.assert_item_error([{"price": 1, "category": "Clothing"}], "items[0].name")
        self.assert_item_error([item(name="  ")], "items[0].name")
        self.assert_item_error([item(name=123)], "items[0].name")

    def test_bad_prices(self):
        for bad in (-1, "abc", None, True, [], float("nan"), float("inf"), "1.234"):
            with self.subTest(price=bad):
                self.assert_item_error([item(price=bad)], "items[0].price")

    def test_unknown_or_missing_category(self):
        for bad in ("Food", None, 5, "", []):
            with self.subTest(category=bad):
                self.assert_item_error([item(category=bad)], "items[0].category")

    def test_category_is_required_on_every_item(self):
        # even when no campaign uses the category
        self.assert_item_error([item(), {"name": "Hat", "price": 250}], "items[1].category")

    def test_bad_quantity(self):
        for bad in (0, -1, 1.5, "2", True, None):
            with self.subTest(quantity=bad):
                self.assert_item_error([item(quantity=bad)], "items[0].quantity")

    def test_error_points_at_the_right_item(self):
        self.assert_item_error([item(), item(price=-5)], "items[1].price")

    def test_absurdly_large_price_is_reported_not_crashed(self):
        with self.assertRaises(DiscountError) as ctx:
            calculate([item(price=10**40)], [])
        self.assertEqual(ctx.exception.code, "NUMBER_OUT_OF_RANGE")


class CampaignValidation(unittest.TestCase):
    def assert_campaign_error(self, campaigns, field=None, code="INVALID_CAMPAIGN"):
        with self.assertRaises(InvalidCampaignError) as ctx:
            calculate(CART_600, campaigns)
        self.assertEqual(ctx.exception.code, code)
        if field:
            self.assertEqual(ctx.exception.field, field)

    def test_campaigns_must_be_a_list_of_objects(self):
        self.assert_campaign_error("fixed_amount", "campaigns")
        self.assert_campaign_error({"type": "points"}, "campaigns")
        self.assert_campaign_error(["points"], "campaigns[0]")

    def test_unknown_or_missing_type(self):
        for bad in ({"type": "bogus"}, {}, {"type": None}, {"type": 5}, {"type": ["points"]}):
            with self.subTest(campaign=bad):
                self.assert_campaign_error([bad], "campaigns[0].type", "UNKNOWN_CAMPAIGN_TYPE")

    def test_duplicate_category_is_rejected_with_clear_error(self):
        campaigns = [
            {"type": "fixed_amount", "amount": 50},
            {"type": "percentage", "percentage": 10},
        ]
        with self.assertRaises(DuplicateCategoryError) as ctx:
            calculate(CART_600, campaigns)
        self.assertEqual(ctx.exception.code, "DUPLICATE_CATEGORY")
        self.assertEqual(ctx.exception.field, "campaigns[1]")
        self.assertIn("coupon", ctx.exception.message)

    def test_same_campaign_twice_is_a_duplicate(self):
        with self.assertRaises(DuplicateCategoryError):
            calculate(CART_600, [{"type": "points", "points": 1}, {"type": "points", "points": 2}])

    def test_fixed_amount_parameters(self):
        for bad in (-1, "x", None, True, 1.005):
            with self.subTest(amount=bad):
                self.assert_campaign_error([{"type": "fixed_amount", "amount": bad}], "campaigns[0].amount")
        self.assert_campaign_error([{"type": "fixed_amount"}], "campaigns[0].amount")

    def test_percentage_parameters(self):
        for bad in (-1, 100.01, 101, "x", None, True):
            with self.subTest(percentage=bad):
                self.assert_campaign_error([{"type": "percentage", "percentage": bad}], "campaigns[0].percentage")

    def test_category_percentage_parameters(self):
        self.assert_campaign_error(
            [{"type": "category_percentage", "category": "Food", "amount": 10}], "campaigns[0].category"
        )
        self.assert_campaign_error(
            [{"type": "category_percentage", "category": "Clothing", "amount": 150}], "campaigns[0].amount"
        )

    def test_category_percentage_needs_amount_parameter(self):
        self.assert_campaign_error(
            [{"type": "category_percentage", "category": "Clothing", "percentage": 10}], "campaigns[0].amount"
        )

    def test_points_parameters(self):
        for bad in (-1, 1.5, "10", None, True):
            with self.subTest(points=bad):
                self.assert_campaign_error([{"type": "points", "points": bad}], "campaigns[0].points")

    def test_seasonal_parameters(self):
        self.assert_campaign_error([{"type": "seasonal", "every": 0, "discount": 40}], "campaigns[0].every")
        self.assert_campaign_error([{"type": "seasonal", "every": -300, "discount": 40}], "campaigns[0].every")
        self.assert_campaign_error([{"type": "seasonal", "every": 300, "discount": -1}], "campaigns[0].discount")
        self.assert_campaign_error([{"type": "seasonal", "every": 300}], "campaigns[0].discount")

    def test_error_reports_original_input_position_not_sorted_position(self):
        campaigns = [
            {"type": "seasonal", "every": 300, "discount": 40},
            {"type": "points", "points": -5},  # index 1, even though it is applied first
        ]
        self.assert_campaign_error(campaigns, "campaigns[1].points")

    def test_error_to_dict_shape(self):
        with self.assertRaises(DiscountError) as ctx:
            calculate(CART_600, [{"type": "bogus"}])
        self.assertEqual(
            ctx.exception.to_dict()["error"],
            {
                "code": "UNKNOWN_CAMPAIGN_TYPE",
                "message": ctx.exception.message,
                "field": "campaigns[0].type",
            },
        )


class CommandLine(unittest.TestCase):
    def run_cli(self, path):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main([str(path)])
        return code, json.loads(buffer.getvalue())  # stdout must be pure JSON

    def write_json(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "input.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_example_files(self):
        expected = {
            "01_fixed_amount.json": 550.0,
            "02_percentage.json": 540.0,
            "03_category_percentage.json": 2382.5,
            "04_points.json": 762.0,
            "05_seasonal.json": 750.0,
            "06_all_campaigns.json": 942.0,
        }
        for name, price in expected.items():
            with self.subTest(example=name):
                code, output = self.run_cli(EXAMPLES / name)
                self.assertEqual(code, 0)
                self.assertEqual(output["status"], "ok")
                self.assertEqual(output["final_price"], price)

    def test_invalid_example_exits_with_error_json(self):
        code, output = self.run_cli(EXAMPLES / "07_invalid_duplicate_coupon.json")
        self.assertEqual(code, 1)
        self.assertEqual(output["status"], "error")
        self.assertEqual(output["error"]["code"], "DUPLICATE_CATEGORY")

    def test_missing_file(self):
        code, output = self.run_cli(EXAMPLES / "does_not_exist.json")
        self.assertEqual((code, output["error"]["code"]), (1, "FILE_NOT_FOUND"))

    def test_invalid_json(self):
        code, output = self.run_cli(self.write_json("{not json"))
        self.assertEqual((code, output["error"]["code"]), (1, "INVALID_JSON"))

    def test_top_level_must_be_an_object(self):
        code, output = self.run_cli(self.write_json("[1, 2, 3]"))
        self.assertEqual((code, output["error"]["code"]), (1, "INVALID_INPUT"))

    def test_missing_items_key(self):
        code, output = self.run_cli(self.write_json('{"campaigns": []}'))
        self.assertEqual((code, output["error"]["code"]), (1, "INVALID_ITEM"))

    def test_nan_in_json_is_rejected(self):
        # Python's json accepts NaN; our validation must not.
        text = '{"items": [{"name": "A", "price": NaN, "category": "Clothing"}]}'
        code, output = self.run_cli(self.write_json(text))
        self.assertEqual((code, output["error"]["field"]), (1, "items[0].price"))


if __name__ == "__main__":
    unittest.main()
