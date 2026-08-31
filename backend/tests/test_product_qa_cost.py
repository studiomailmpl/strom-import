"""
Unit tests for the invoice-vs-order-confirmation cost check.
"""

import pytest

from app.services import product_qa as qa
from app.services import order_matching as om


def product_with_costs(invoice, order):
    return {
        "_invoice_cost_price_eur": invoice,
        "_order_confirmation_wholesale_price": order,
    }


class TestCostMismatchCheck:
    def test_gap_above_tolerance_is_flagged(self):
        warnings = qa._check_cost_vs_order_confirmation(product_with_costs(100.0, 110.0))
        assert len(warnings) == 1
        w = warnings[0]
        assert w["level"] == "warning"
        assert w["code"] == "cost_mismatch_order_confirmation"
        assert w["field"] == "cost_price_eur"
        assert w["message"].startswith("Prisafvigelse: faktura €100.00, ordrebekræftelse €110.00")

    def test_gap_within_tolerance_is_silent(self):
        assert qa._check_cost_vs_order_confirmation(product_with_costs(100.0, 101.0)) == []

    def test_exactly_at_the_tolerance_is_silent(self):
        assert qa._check_cost_vs_order_confirmation(product_with_costs(100.0, 102.0)) == []

    def test_a_currency_mix_up_is_caught(self):
        """A DKK figure read as EUR lands ~7x out — the case this check exists for."""
        warnings = qa._check_cost_vs_order_confirmation(product_with_costs(614.6, 82.44))
        assert len(warnings) == 1
        assert "86.6%" in warnings[0]["message"]

    def test_direction_does_not_matter(self):
        assert len(qa._check_cost_vs_order_confirmation(product_with_costs(110.0, 100.0))) == 1

    @pytest.mark.parametrize(
        "invoice,order",
        [(None, 100.0), (100.0, None), (None, None), ("abc", 100.0), (0, 0)],
    )
    def test_incomparable_values_produce_nothing(self, invoice, order):
        assert qa._check_cost_vs_order_confirmation(product_with_costs(invoice, order)) == []

    def test_unmatched_products_are_not_flagged(self):
        """No confirmation means nothing to compare — not a warning."""
        assert qa._check_cost_vs_order_confirmation({"cost_price_eur": 100.0}) == []

    def test_the_check_runs_as_part_of_validate_product(self):
        p = {
            "title": "Wool Coat Navy", "style_code": "ABC123",
            "_invoice_cost_price_eur": 100.0,
            "_order_confirmation_wholesale_price": 130.0,
        }
        codes = [w["code"] for w in qa.validate_product(p)]
        assert "cost_mismatch_order_confirmation" in codes

    def test_tolerance_is_shared_with_the_merge_policy(self):
        assert qa.COST_TOLERANCE == om.COST_TOLERANCE


class TestWarningsSurviveTheQaPass:
    def test_merge_time_warnings_are_not_wiped(self):
        """QA runs after the merge; assigning outright would drop its warnings."""
        existing = {
            "level": "warning", "code": "some_earlier_check",
            "field": "variants", "message": "fra tidligere trin",
        }
        products = [{"title": "Wool Coat Navy", "style_code": "A", "qa_warnings": [existing]}]

        qa.validate_products(products)
        assert existing in products[0]["qa_warnings"]

    def test_the_same_warning_is_not_listed_twice(self):
        """The merge and this check both raise it when linking outside the pipeline."""
        duplicate = {
            "level": "warning", "code": "cost_mismatch_order_confirmation",
            "field": "cost_price_eur", "message": "fra merge",
        }
        products = [{
            "title": "Wool Coat Navy", "style_code": "A",
            "_invoice_cost_price_eur": 100.0,
            "_order_confirmation_wholesale_price": 130.0,
            "qa_warnings": [duplicate],
        }]

        qa.validate_products(products)
        matching = [
            w for w in products[0]["qa_warnings"]
            if w["code"] == "cost_mismatch_order_confirmation"
        ]
        assert len(matching) == 1
        assert "Prisafvigelse" in matching[0]["message"], "QA's own wording wins"


class TestMergeStashesBothCosts:
    def test_the_invoice_cost_survives_being_overwritten(self):
        """Without this the QA comparison would always come out at zero."""
        import uuid
        from app.models.import_product import ImportProduct
        from app.models.order_confirmation import OrderConfirmationLine

        p = ImportProduct(
            id=uuid.uuid4(), title="", vendor="", style_code="A",
            color="", color_code="", color_original="", season="",
            variants=[], cost_price_eur=82.44, images=[], description_da="",
            qa_warnings=[], data_sources={},
        )
        line = OrderConfirmationLine(
            id=uuid.uuid4(), style_number="A", size="M", wholesale_price=95.00,
        )

        om.merge_with_order_data(p, [line])

        assert p.cost_price_eur == 95.00, "the confirmation wins, per the policy"
        assert p._invoice_cost_price_eur == 82.44
        assert p._order_confirmation_wholesale_price == 95.00
