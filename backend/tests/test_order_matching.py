"""
Unit tests for matching invoice products to order confirmation lines.

Uses the real ImportProduct and OrderConfirmationLine models, instantiated
without a session, so the tests bind to the actual attribute names.
"""

import uuid

import pytest

from app.models.import_product import ImportProduct
from app.models.order_confirmation import OrderConfirmationLine
from app.services import order_matching as om


# ═══════════════════════════════════════════════
# Builders
# ═══════════════════════════════════════════════

def product(**kwargs) -> ImportProduct:
    defaults = {
        "id": uuid.uuid4(),
        "title": "",
        "vendor": "",
        "style_code": "",
        "color": "",
        "color_code": "",
        "color_original": "",
        "season": "",
        "variants": [],
        "cost_price_eur": None,
        "retail_price_dkk": None,
        "images": [],
        "description_da": "",
        "qa_warnings": [],
        "data_sources": {},
    }
    defaults.update(kwargs)
    return ImportProduct(**defaults)


def line(**kwargs) -> OrderConfirmationLine:
    defaults = {
        "id": uuid.uuid4(),
        "style_number": None,
        "sku": None,
        "ean": None,
        "product_name": None,
        "color_code": None,
        "color_name": None,
        "size": None,
        "quantity": None,
        "wholesale_price": None,
        "rrp": None,
    }
    defaults.update(kwargs)
    return OrderConfirmationLine(**defaults)


# ═══════════════════════════════════════════════
# Normalisation
# ═══════════════════════════════════════════════

class TestNormalizeIdentifier:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("COHBU-M26388", "COHBUM26388"),
            ("cohbu_m26388", "COHBUM26388"),
            ("COHBU.M26388", "COHBUM26388"),
            ("COHBU M26388", "COHBUM26388"),
            ("0012", "12"),
            ("000", "000"),          # all zeros must not vanish entirely
            ("", ""),
            (None, ""),
        ],
    )
    def test_reduces_to_a_comparable_core(self, raw, expected):
        assert om.normalize_identifier(raw) == expected

    def test_separator_variants_all_agree(self):
        forms = ["COHBU-M26388", "cohbu_m26388", "COHBU.M26388", "COHBU M26388"]
        assert len({om.normalize_identifier(f) for f in forms}) == 1


# ═══════════════════════════════════════════════
# Step 1 — exact
# ═══════════════════════════════════════════════

class TestExactMatch:
    def test_style_code_against_style_number(self):
        p = product(style_code="I036159")
        l = line(style_number="I036159")
        assert om.score_pair(p, l) == (100, om.METHOD_EXACT)

    def test_is_case_insensitive(self):
        p = product(style_code="i036159")
        l = line(style_number="I036159")
        assert om.score_pair(p, l) == (100, om.METHOD_EXACT)

    def test_style_code_against_the_lines_sku(self):
        p = product(style_code="I036159")
        l = line(style_number=None, sku="I036159")
        assert om.score_pair(p, l) == (100, om.METHOD_EXACT)

    def test_surrounding_whitespace_is_ignored(self):
        p = product(style_code="  I036159 ")
        l = line(style_number="I036159")
        assert om.score_pair(p, l) == (100, om.METHOD_EXACT)


# ═══════════════════════════════════════════════
# Step 2 — normalized
# ═══════════════════════════════════════════════

class TestNormalizedMatch:
    @pytest.mark.parametrize(
        "invoice,confirmation",
        [
            ("COHBU-M26388", "COHBUM26388"),
            ("COHBU_M26388", "COHBU-M26388"),
            ("COHBU.M26388", "cohbu m26388"),
            ("0012345", "12345"),
        ],
    )
    def test_matches_once_separators_and_leading_zeros_go(self, invoice, confirmation):
        result = om.score_pair(product(style_code=invoice), line(style_number=confirmation))
        assert result == (90, om.METHOD_NORMALIZED)

    def test_genuinely_different_codes_do_not_match(self):
        assert om.score_pair(product(style_code="ABC123"), line(style_number="XYZ789")) is None


# ═══════════════════════════════════════════════
# Step 3 — colour code
# ═══════════════════════════════════════════════

class TestColorCodeMatch:
    def test_colour_suffix_on_the_confirmation_side(self):
        """Invoice has the plain style; the confirmation appends the colour."""
        p = product(style_code="ABC123", color="210 Blue")
        l = line(style_number="ABC123-210", color_code="210")
        assert om.score_pair(p, l) == (80, om.METHOD_COLOR_CODE)

    def test_colour_code_inside_the_products_colour_text(self):
        assert om.colours_agree(product(color="210 Blue"), line(color_code="210"))

    def test_colour_name_also_counts(self):
        assert om.colours_agree(product(color="Dark Navy"), line(color_name="Navy"))

    def test_disagreeing_colour_blocks_the_prefix_match(self):
        p = product(style_code="ABC123", color="999 Red")
        l = line(style_number="ABC123-210", color_code="210")
        assert om.score_pair(p, l) is None

    def test_a_product_with_no_colour_does_not_agree(self):
        assert not om.colours_agree(product(color=""), line(color_code="210"))

    def test_short_codes_are_not_prefix_matched(self):
        """Two- and three-character codes prefix-match far too eagerly."""
        p = product(style_code="AB", color="210")
        l = line(style_number="ABC123", color_code="210")
        assert om.score_pair(p, l) is None


# ═══════════════════════════════════════════════
# Step 4 — fuzzy
# ═══════════════════════════════════════════════

class TestFuzzyMatch:
    def test_near_identical_titles_match(self):
        p = product(style_code="NOMATCH1", title="Mello Knit Shirt Dark Navy", vendor="Carhartt")
        l = line(style_number="OTHER", product_name="Mello Knit Shirt")
        score, method = om.score_pair(p, l)
        assert method == om.METHOD_FUZZY
        assert score > om.FUZZY_THRESHOLD

    def test_unrelated_titles_do_not_match(self):
        p = product(style_code="NOMATCH1", title="Wool Overcoat")
        l = line(style_number="OTHER", product_name="Cotton Socks")
        assert om.score_pair(p, l) is None

    def test_fuzzy_is_blocked_across_vendors(self):
        """"Wool Coat" is not distinctive enough to match across brands."""
        p = product(style_code="NOMATCH1", title="Wool Coat", vendor="Marni")
        l = line(style_number="OTHER", product_name="Wool Coat")

        assert om.score_pair(p, l, vendor_season_ok=True) is not None
        assert om.score_pair(p, l, vendor_season_ok=False) is None

    def test_vendor_and_season_gate(self):
        p = product(vendor="Marni", season="AW26")
        assert om._same_vendor_and_season(p, "Marni", "AW26")
        assert not om._same_vendor_and_season(p, "Carhartt", "AW26")
        assert not om._same_vendor_and_season(p, "Marni", "SS27")

    def test_gate_passes_when_the_confirmation_states_nothing(self):
        assert om._same_vendor_and_season(product(vendor="Marni"), None, None)


# ═══════════════════════════════════════════════
# Matching across a batch
# ═══════════════════════════════════════════════

class TestMatchProductsToOrderLines:
    def test_one_result_per_product_and_the_strongest_wins(self):
        p = product(style_code="ABC123", title="Wool Coat", vendor="Marni")
        exact = line(style_number="ABC123")
        weaker = line(style_number="ABC-123")

        results = om.match_products_to_order_lines([p], [weaker, exact])
        assert len(results) == 1
        assert results[0].order_line_id == exact.id
        assert results[0].confidence == 100
        assert results[0].match_method == om.METHOD_EXACT

    def test_colour_breaks_a_tie_between_two_colourways(self):
        """Both lines share the style; only the colour tells them apart."""
        p = product(style_code="ABC123", color="210 Blue")
        blue = line(style_number="ABC123", color_code="210", size="M")
        red = line(style_number="ABC123", color_code="999", size="M")

        results = om.match_products_to_order_lines([p], [red, blue])
        assert results[0].order_line_id == blue.id

    def test_unmatched_products_are_absent(self):
        results = om.match_products_to_order_lines(
            [product(style_code="NOPE", title="Nothing Alike")],
            [line(style_number="ABC123", product_name="Wool Coat")],
        )
        assert results == []

    def test_records_the_product_id(self):
        p = product(style_code="ABC123")
        results = om.match_products_to_order_lines([p], [line(style_number="ABC123")])
        assert results[0].product_id == p.id

    def test_empty_inputs_are_safe(self):
        assert om.match_products_to_order_lines([], []) == []
        assert om.match_products_to_order_lines([product(style_code="A")], []) == []


# ═══════════════════════════════════════════════
# Variant merging
# ═══════════════════════════════════════════════

class TestMergeVariants:
    def test_sizes_from_the_confirmation_quantities_from_the_invoice(self):
        invoice = [{"size": "M", "quantity": 4}, {"size": "L", "quantity": 2}]
        lines = [line(size="M"), line(size="L")]

        merged = om.merge_variants(invoice, lines)
        assert merged == [
            {"size": "M", "quantity": 4},
            {"size": "L", "quantity": 2},
        ]

    def test_ordered_but_undelivered_size_is_kept_at_zero(self):
        merged = om.merge_variants(
            [{"size": "M", "quantity": 4}], [line(size="M"), line(size="XL")]
        )
        assert {"size": "XL", "quantity": 0} in merged

    def test_delivered_size_missing_from_the_confirmation_is_not_lost(self):
        """Dropping it would discard stock that actually arrived."""
        merged = om.merge_variants(
            [{"size": "M", "quantity": 4}, {"size": "XXL", "quantity": 1}],
            [line(size="M")],
        )
        sizes = {v["size"]: v["quantity"] for v in merged}
        assert sizes == {"M": 4, "XXL": 1}

    def test_confirmation_spelling_of_a_size_wins(self):
        merged = om.merge_variants([{"size": "m", "quantity": 4}], [line(size="M")])
        assert merged[0]["size"] == "M"
        assert merged[0]["quantity"] == 4

    def test_invoice_ean_is_preserved(self):
        merged = om.merge_variants(
            [{"size": "M", "quantity": 4, "ean": "40584598"}], [line(size="M")]
        )
        assert merged[0]["ean"] == "40584598"


# ═══════════════════════════════════════════════
# Merge policy
# ═══════════════════════════════════════════════

class TestMergeWithOrderData:
    def test_confirmation_wins_for_identity(self):
        p = product(style_code="abc-123", title="ABC123", color_code="")
        lines = [line(
            style_number="ABC123", product_name="Mello Knit Shirt",
            color_code="3ONXX", color_name="Dark Navy", size="M",
        )]
        outcome = om.merge_with_order_data(p, lines)

        assert p.style_code == "ABC123"
        assert p.title == "Mello Knit Shirt"
        assert p.color_code == "3ONXX"
        assert outcome.data_sources["title"] == om.SOURCE_ORDER_CONFIRMATION

    def test_invoice_wins_for_quantity(self):
        p = product(variants=[{"size": "M", "quantity": 4}])
        lines = [line(style_number="A", size="M", quantity=10)]
        outcome = om.merge_with_order_data(p, lines)

        assert p.variants[0]["quantity"] == 4, "delivered beats ordered"
        assert outcome.data_sources["quantity"] == om.SOURCE_INVOICE
        assert outcome.data_sources["size_range"] == om.SOURCE_ORDER_CONFIRMATION

    def test_images_and_descriptions_are_left_to_the_scraper(self):
        p = product(images=["http://x/1.jpg"], description_da="Beskrivelse")
        lines = [line(style_number="A", product_name="Name", size="M")]
        outcome = om.merge_with_order_data(p, lines)

        assert p.images == ["http://x/1.jpg"]
        assert p.description_da == "Beskrivelse"
        assert outcome.data_sources["images"] == om.SOURCE_WEB
        assert outcome.data_sources["description_da"] == om.SOURCE_WEB

    def test_cost_gap_above_two_percent_raises_a_warning(self):
        p = product(cost_price_eur=100.0)
        lines = [line(style_number="A", size="M", wholesale_price=110.0)]
        outcome = om.merge_with_order_data(p, lines)

        assert len(outcome.warnings) == 1
        warning = outcome.warnings[0]
        assert warning["code"] == "cost_mismatch_order_confirmation"
        assert warning["level"] == "warning"
        assert "100.00" in warning["message"] and "110.00" in warning["message"]
        assert warning in p.qa_warnings

    def test_cost_gap_within_tolerance_is_silent(self):
        p = product(cost_price_eur=100.0)
        lines = [line(style_number="A", size="M", wholesale_price=101.0)]
        outcome = om.merge_with_order_data(p, lines)
        assert outcome.warnings == []

    def test_missing_cost_on_either_side_is_not_a_warning(self):
        p = product(cost_price_eur=None)
        outcome = om.merge_with_order_data(p, [line(style_number="A", wholesale_price=99.0)])
        assert outcome.warnings == []

    def test_rrp_becomes_the_retail_price(self):
        """The confirmation's RRP is the real retail price, not an estimate."""
        p = product()
        lines = [line(style_number="A", size="M", rrp=499.0)]
        outcome = om.merge_with_order_data(p, lines, currency="DKK", eur_rate=7.46)

        assert p.retail_price_dkk == 499.0
        assert outcome.data_sources["rrp"] == om.SOURCE_ORDER_CONFIRMATION

    def test_a_eur_rrp_is_converted(self):
        p = product()
        lines = [line(style_number="A", size="M", rrp=100.0)]
        om.merge_with_order_data(p, lines, currency="EUR", eur_rate=7.46)
        assert p.retail_price_dkk == pytest.approx(746.0)

    def test_an_unconvertible_rrp_claims_nothing(self):
        """Provenance must not be claimed for a value that was never applied."""
        p = product(retail_price_dkk=1234.0)
        lines = [line(style_number="A", size="M", rrp=499.0)]
        outcome = om.merge_with_order_data(p, lines, currency="JPY", eur_rate=7.46)

        assert p.retail_price_dkk == 1234.0, "the calculated price stands"
        assert "rrp" not in outcome.data_sources

    def test_no_currency_means_no_conversion(self):
        p = product(retail_price_dkk=1234.0)
        lines = [line(style_number="A", size="M", rrp=499.0)]
        outcome = om.merge_with_order_data(p, lines)
        assert p.retail_price_dkk == 1234.0
        assert "rrp" not in outcome.data_sources

    def test_match_bookkeeping_is_written_to_the_product(self):
        p = product()
        l = line(style_number="A", size="M")
        match = om.MatchResult(
            product_id=p.id, order_line_id=l.id,
            confidence=90, match_method=om.METHOD_NORMALIZED,
        )
        om.merge_with_order_data(p, [l], match=match)

        assert p.order_confirmation_line_id == l.id
        assert p.match_confidence == 90
        assert p.match_method == om.METHOD_NORMALIZED

    def test_existing_data_sources_are_extended_not_replaced(self):
        p = product(data_sources={"handle": "manual"})
        om.merge_with_order_data(p, [line(style_number="A", product_name="N", size="M")])
        assert p.data_sources["handle"] == "manual"
        assert p.data_sources["title"] == om.SOURCE_ORDER_CONFIRMATION

    def test_no_lines_changes_nothing(self):
        p = product(style_code="ORIGINAL")
        outcome = om.merge_with_order_data(p, [])
        assert p.style_code == "ORIGINAL"
        assert outcome.changed_fields == []

    def test_blank_confirmation_fields_do_not_erase_invoice_data(self):
        p = product(style_code="ABC123", title="Good Title")
        om.merge_with_order_data(p, [line(style_number="", product_name="", size="M")])
        assert p.style_code == "ABC123"
        assert p.title == "Good Title"


# ═══════════════════════════════════════════════
# Grouping the size run
# ═══════════════════════════════════════════════

class TestGroupLinesByMatch:
    def test_a_match_expands_to_the_whole_size_run(self):
        p = product(style_code="ABC123", color="210 Blue")
        sizes = [
            line(style_number="ABC123", color_code="210", size="S"),
            line(style_number="ABC123", color_code="210", size="M"),
            line(style_number="ABC123", color_code="210", size="L"),
        ]
        other_colour = line(style_number="ABC123", color_code="999", size="M")

        matches = om.match_products_to_order_lines([p], sizes + [other_colour])
        grouped = om.group_lines_by_match(matches, sizes + [other_colour])

        assert len(grouped[p.id]) == 3
        assert {l.size for l in grouped[p.id]} == {"S", "M", "L"}
        assert other_colour not in grouped[p.id]

    def test_end_to_end_match_then_merge(self):
        p = product(
            style_code="abc-123", title="ABC123", color="210 Blue",
            cost_price_eur=82.44, variants=[{"size": "M", "quantity": 4}],
        )
        sizes = [
            line(style_number="ABC123", color_code="210", size="M",
                 product_name="Mello Knit Shirt", wholesale_price=82.44, rrp=199.0),
            line(style_number="ABC123", color_code="210", size="L",
                 product_name="Mello Knit Shirt", wholesale_price=82.44, rrp=199.0),
        ]

        matches = om.match_products_to_order_lines([p], sizes)
        grouped = om.group_lines_by_match(matches, sizes)
        outcome = om.merge_with_order_data(
            p, grouped[p.id], match=matches[0], currency="DKK", eur_rate=7.46
        )

        assert p.title == "Mello Knit Shirt"
        assert p.match_confidence == 90  # "abc-123" vs "ABC123" is a normalised match
        assert {v["size"]: v["quantity"] for v in p.variants} == {"M": 4, "L": 0}
        assert outcome.warnings == []
        assert p.data_sources["rrp"] == om.SOURCE_ORDER_CONFIRMATION


# ═══════════════════════════════════════════════
# ProductProxy — the bridge to the pipeline's dicts
# ═══════════════════════════════════════════════

class TestProductProxy:
    def test_reads_attributes_from_the_dict(self):
        proxy = om.ProductProxy({"style_code": "ABC123", "color": "210 Blue"})
        assert proxy.style_code == "ABC123"
        assert proxy.color == "210 Blue"

    def test_missing_keys_read_as_none(self):
        assert om.ProductProxy({}).style_code is None

    def test_writes_go_back_into_the_same_dict(self):
        data = {"style_code": "old"}
        proxy = om.ProductProxy(data)
        proxy.style_code = "new"
        assert data["style_code"] == "new", "the pipeline must see the merge"

    def test_a_dict_without_an_id_gets_a_stable_synthetic_one(self):
        proxy = om.ProductProxy({})
        assert proxy.id is not None
        assert proxy.id == proxy.id

    def test_two_proxies_get_different_ids(self):
        assert om.ProductProxy({}).id != om.ProductProxy({}).id

    def test_an_existing_id_is_respected(self):
        existing = uuid.uuid4()
        assert om.ProductProxy({"id": existing}).id == existing

    def test_matching_works_on_dicts(self):
        data = {"style_code": "ABC123", "vendor": "Marni", "color": "210 Blue"}
        proxy = om.ProductProxy(data)
        results = om.match_products_to_order_lines([proxy], [line(style_number="ABC123")])
        assert len(results) == 1
        assert results[0].product_id == proxy.id
        assert results[0].confidence == 100

    def test_merging_writes_the_whole_policy_into_the_dict(self):
        data = {
            "style_code": "abc-123",
            "title": "ABC123",
            "color": "210 Blue",
            "cost_price_eur": 82.44,
            "variants": [{"size": "M", "quantity": 4}],
            "qa_warnings": [],
            "data_sources": {},
        }
        proxy = om.ProductProxy(data)
        sizes = [
            line(style_number="ABC123", color_code="210", size="M",
                 product_name="Mello Knit Shirt", wholesale_price=82.44, rrp=199.0),
            line(style_number="ABC123", color_code="210", size="L",
                 product_name="Mello Knit Shirt", wholesale_price=82.44, rrp=199.0),
        ]

        matches = om.match_products_to_order_lines([proxy], sizes)
        grouped = om.group_lines_by_match(matches, sizes)
        om.merge_with_order_data(
            proxy, grouped[proxy.id], match=matches[0], currency="DKK", eur_rate=7.46
        )

        # Everything lands in the plain dict the pipeline will save from.
        assert data["title"] == "Mello Knit Shirt"
        assert data["style_code"] == "ABC123"
        assert data["match_confidence"] == 90
        assert data["match_method"] == om.METHOD_NORMALIZED
        assert data["order_confirmation_line_id"] == matches[0].order_line_id
        assert data["data_sources"]["rrp"] == om.SOURCE_ORDER_CONFIRMATION
        assert {v["size"]: v["quantity"] for v in data["variants"]} == {"M": 4, "L": 0}

    def test_cost_warning_appends_to_the_dicts_qa_warnings(self):
        data = {"style_code": "A", "cost_price_eur": 100.0, "qa_warnings": [
            {"level": "info", "code": "existing", "field": "x", "message": "keep me"}
        ]}
        proxy = om.ProductProxy(data)
        om.merge_with_order_data(proxy, [line(style_number="A", wholesale_price=120.0)])

        codes = [w["code"] for w in data["qa_warnings"]]
        assert "existing" in codes, "existing warnings must survive"
        assert "cost_mismatch_order_confirmation" in codes
