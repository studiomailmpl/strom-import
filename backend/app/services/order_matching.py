"""
Match invoice products to order confirmation lines, and merge the two sources.

An invoice says what arrived and what it cost. An order confirmation says what
was ordered, in which sizes, and — uniquely — what it should retail for. The
two have to be tied together per product before either is useful.

Matching runs four ways, strongest first, and keeps the best result per product:

    exact_sku    100  an identifier matches verbatim
    normalized    90  identifiers match once separators and leading zeros go
    color_code    80  style matches as a prefix and the colour codes agree
    fuzzy      86-100  titles match within the same vendor and season

When several lines match equally well, the one whose colour agrees with the
product wins — a style number alone does not distinguish two colourways.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# Match methods, in descending strength.
METHOD_EXACT = "exact_sku"
METHOD_NORMALIZED = "normalized"
METHOD_COLOR_CODE = "color_code"
METHOD_FUZZY = "fuzzy"

CONFIDENCE_EXACT = 100
CONFIDENCE_NORMALIZED = 90
CONFIDENCE_COLOR_CODE = 80

# rapidfuzz token_set_ratio must beat this for a title match to count.
FUZZY_THRESHOLD = 85

# Cost difference between invoice and confirmation that is worth flagging.
COST_TOLERANCE = 0.02

SOURCE_ORDER_CONFIRMATION = "order_confirmation"
SOURCE_INVOICE = "invoice"
SOURCE_WEB = "web"


@dataclass
class MatchResult:
    product_id: uuid.UUID
    order_line_id: uuid.UUID
    confidence: int
    match_method: str


class ProductProxy:
    """
    Attribute access over a product dict.

    The import pipeline carries products as plain dicts until they are saved,
    while matching and merging are written against ImportProduct rows. This
    bridges the two rather than duplicating either. Reads and writes go straight
    through to the underlying dict, so merging a proxy updates the pipeline's
    product in place.

    Dicts have no primary key yet, so the proxy carries a synthetic id purely to
    correlate MatchResults back to products.
    """

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "_id", data.get("id") or uuid.uuid4())

    def __getattr__(self, name):
        if name == "id":
            return self.__dict__["_id"]
        return self.__dict__["_data"].get(name)

    def __setattr__(self, name, value):
        self.__dict__["_data"][name] = value

    def __repr__(self) -> str:
        return f"<ProductProxy {self.__dict__['_data'].get('style_code')}>"


@dataclass
class MergeOutcome:
    """What merge_with_order_data changed, for logging and for the review UI."""

    changed_fields: list[str] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    data_sources: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════
# Normalisation
# ═══════════════════════════════════════════════

def normalize_identifier(value: str | None) -> str:
    """
    Reduce a style number or SKU to its comparable core.

    Drops separators and leading zeros, so "COHBU-M26388", "cohbu_m26388" and
    "COHBU.M26388" all compare equal, and "0012" matches "12".
    """
    if not value:
        return ""
    stripped = re.sub(r"[\s\-_./\\]", "", str(value)).upper()
    if not stripped:
        return ""
    # Leading zeros only carry meaning in fixed-width codes, and the two sides
    # rarely agree on the width.
    return stripped.lstrip("0") or stripped


def _identifiers(*values) -> list[str]:
    """Non-empty, trimmed, upper-cased identifiers from the values given."""
    out = []
    for value in values:
        if value and str(value).strip():
            out.append(str(value).strip().upper())
    return out


def product_identifiers(product) -> list[str]:
    # ImportProduct stores the article number in style_code; `sku` is accepted
    # too so the function also works on dicts carrying one.
    return _identifiers(
        getattr(product, "style_code", None),
        getattr(product, "sku", None),
    )


def line_identifiers(line) -> list[str]:
    return _identifiers(
        getattr(line, "style_number", None),
        getattr(line, "sku", None),
    )


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {t for t in re.split(r"[^A-Za-z0-9]+", str(text).upper()) if t}


def colours_agree(product, line) -> bool:
    """
    True when the line's colour code appears in the product's colour fields.

    A product coloured "210 Blue" matches a line with colour code "210" — the
    invoice keeps the supplier's raw colour text, the confirmation splits it.
    """
    line_code = (getattr(line, "color_code", None) or "").strip().upper()
    line_name = (getattr(line, "color_name", None) or "").strip().upper()
    if not line_code and not line_name:
        return False

    haystack = _tokens(" ".join(filter(None, [
        getattr(product, "color", None),
        getattr(product, "color_code", None),
        getattr(product, "color_original", None),
    ])))
    if not haystack:
        return False

    if line_code and line_code in haystack:
        return True
    if line_name and _tokens(line_name) & haystack:
        return True
    return False


# ═══════════════════════════════════════════════
# Scoring
# ═══════════════════════════════════════════════

def _style_prefix_match(product_ids: list[str], line_ids: list[str]) -> bool:
    """
    True when one side's identifier starts with the other's.

    Confirmations often append the colour to the style — "ABC123-210" against
    the invoice's plain "ABC123".
    """
    for p in product_ids:
        np = normalize_identifier(p)
        if len(np) < 4:
            continue
        for l in line_ids:
            nl = normalize_identifier(l)
            if len(nl) < 4:
                continue
            if np != nl and (np.startswith(nl) or nl.startswith(np)):
                return True
    return False


def score_pair(
    product,
    line,
    *,
    vendor_season_ok: bool = True,
    fuzzy_threshold: int = FUZZY_THRESHOLD,
) -> tuple[int, str] | None:
    """
    Score one product against one confirmation line.

    Returns (confidence, method), or None when they do not match at all.
    """
    product_ids = product_identifiers(product)
    line_ids = line_identifiers(line)

    # 1. Exact identifier match.
    if product_ids and line_ids and set(product_ids) & set(line_ids):
        return CONFIDENCE_EXACT, METHOD_EXACT

    # 2. Same identifier once separators and leading zeros are gone.
    normalized_product = {normalize_identifier(p) for p in product_ids} - {""}
    normalized_line = {normalize_identifier(l) for l in line_ids} - {""}
    if normalized_product & normalized_line:
        return CONFIDENCE_NORMALIZED, METHOD_NORMALIZED

    # 3. Style is a prefix of the other side and the colours agree.
    if _style_prefix_match(product_ids, line_ids) and colours_agree(product, line):
        return CONFIDENCE_COLOR_CODE, METHOD_COLOR_CODE

    # 4. Fuzzy title match, but only inside the same vendor and season —
    #    "Wool Coat" is a common enough name to collide across brands.
    if vendor_season_ok:
        title = getattr(product, "title", "") or ""
        name = getattr(line, "product_name", "") or ""
        if title and name:
            ratio = fuzz.token_set_ratio(title, name)
            if ratio > fuzzy_threshold:
                return int(round(ratio)), METHOD_FUZZY

    return None


def _same_vendor_and_season(product, vendor: str | None, season: str | None) -> bool:
    """Fuzzy matching is only allowed within one vendor and season."""
    if vendor:
        product_vendor = (getattr(product, "vendor", "") or "").strip().casefold()
        if product_vendor and product_vendor != vendor.strip().casefold():
            return False
    if season:
        product_season = (
            getattr(product, "season_normalized", None)
            or getattr(product, "season", None)
            or ""
        ).strip().casefold()
        if product_season and product_season != season.strip().casefold():
            return False
    return True


def match_products_to_order_lines(
    products: list,
    order_lines: list,
    *,
    vendor: str | None = None,
    season: str | None = None,
    fuzzy_threshold: int = FUZZY_THRESHOLD,
) -> list[MatchResult]:
    """
    Match each product to its best order confirmation line.

    `vendor` and `season` describe the confirmation as a whole — its lines carry
    neither — and gate the fuzzy step. Products with no match are simply absent
    from the result; one result per product at most.
    """
    results: list[MatchResult] = []

    for product in products:
        vendor_season_ok = _same_vendor_and_season(product, vendor, season)

        best: tuple[int, bool, str, object] | None = None
        for line in order_lines:
            scored = score_pair(
                product,
                line,
                vendor_season_ok=vendor_season_ok,
                fuzzy_threshold=fuzzy_threshold,
            )
            if scored is None:
                continue
            confidence, method = scored
            # Colour agreement breaks ties between two sizes of two colourways
            # that share a style number.
            candidate = (confidence, colours_agree(product, line), method, line)
            if best is None or candidate[:2] > best[:2]:
                best = candidate

        if best is None:
            continue

        confidence, _, method, line = best
        results.append(MatchResult(
            product_id=getattr(product, "id", None),
            order_line_id=getattr(line, "id", None),
            confidence=confidence,
            match_method=method,
        ))

    logger.info(
        "Matched %d of %d product(s) against %d order line(s)",
        len(results), len(products), len(order_lines),
    )
    return results


# ═══════════════════════════════════════════════
# Merge policy
# ═══════════════════════════════════════════════

def _size_key(size: str | None) -> str:
    return re.sub(r"[\s./\\-]", "", (size or "")).upper()


def merge_variants(product_variants: list | None, order_lines: list) -> list[dict]:
    """
    Build the variant list: sizes from the confirmation, quantities from the
    invoice.

    The confirmation defines the size run and spells the labels properly. The
    invoice says what actually turned up, so its quantities win. A size that was
    ordered but not delivered is kept at quantity 0 — it belongs to the size run.
    A size on the invoice but not the confirmation is kept as delivered, since
    dropping it would lose real stock.
    """
    invoice_by_key: dict[str, dict] = {}
    invoice_order: list[str] = []
    for variant in product_variants or []:
        key = _size_key(variant.get("size"))
        if key and key not in invoice_by_key:
            invoice_by_key[key] = dict(variant)
            invoice_order.append(key)

    merged: list[dict] = []
    used: set[str] = set()

    for line in order_lines:
        key = _size_key(getattr(line, "size", None))
        if not key or key in used:
            continue
        used.add(key)
        invoice_variant = invoice_by_key.get(key)
        merged.append({
            # The confirmation's label wins — invoices mis-render sizes.
            "size": (getattr(line, "size", "") or "").strip(),
            "quantity": int(invoice_variant.get("quantity") or 0) if invoice_variant else 0,
            **(
                {"ean": invoice_variant["ean"]}
                if invoice_variant and invoice_variant.get("ean")
                else ({"ean": line.ean} if getattr(line, "ean", None) else {})
            ),
        })

    # Anything delivered that the confirmation never listed.
    for key in invoice_order:
        if key not in used:
            merged.append(dict(invoice_by_key[key]))

    return merged


def _cost_deviates(invoice_cost: float | None, order_cost: float | None) -> float | None:
    """Relative difference between the two costs, or None if not comparable."""
    if invoice_cost is None or order_cost is None:
        return None
    if invoice_cost == 0 and order_cost == 0:
        return 0.0
    denominator = max(abs(invoice_cost), abs(order_cost))
    if denominator == 0:
        return None
    return abs(invoice_cost - order_cost) / denominator


def merge_with_order_data(
    product,
    order_lines: list,
    *,
    match: MatchResult | None = None,
) -> MergeOutcome:
    """
    Apply the merge policy to one product, in place.

    The confirmation wins for identity and pricing — style number, product
    name, colour code, size run, wholesale price and RRP. The invoice wins for
    quantity, because that is what actually arrived. Images and descriptions are
    left alone; web scraping remains their source.

    `order_lines` are all the confirmation lines for this product — one per
    size. Pass the matched line's siblings, not just the matched line, or the
    size run collapses to a single size.

    A cost difference above 2% between the two sources is reported as a QA
    warning rather than silently resolved.
    """
    outcome = MergeOutcome()
    if not order_lines:
        return outcome

    primary = order_lines[0]

    def _apply(attr: str, value, source: str = SOURCE_ORDER_CONFIRMATION):
        if value in (None, ""):
            return
        if getattr(product, attr, None) == value:
            # Already agrees — still record where it came from.
            outcome.data_sources[attr] = source
            return
        setattr(product, attr, value)
        outcome.changed_fields.append(attr)
        outcome.data_sources[attr] = source

    # ── Identity: the confirmation is the cleaner source ──
    _apply("style_code", (primary.style_number or primary.sku or "").strip())
    _apply("title", (primary.product_name or "").strip())
    _apply("color_code", (primary.color_code or "").strip())
    if primary.color_name and not (getattr(product, "color_original", "") or "").strip():
        _apply("color_original", primary.color_name.strip())

    # ── Sizes from the confirmation, quantities from the invoice ──
    merged_variants = merge_variants(getattr(product, "variants", None), order_lines)
    if merged_variants:
        product.variants = merged_variants
        outcome.changed_fields.append("variants")
        outcome.data_sources["size_range"] = SOURCE_ORDER_CONFIRMATION
        outcome.data_sources["quantity"] = SOURCE_INVOICE

    # ── Pricing ──
    invoice_cost = getattr(product, "cost_price_eur", None)
    order_cost = primary.wholesale_price

    # Keep both figures. The merge below overwrites cost_price_eur with the
    # confirmation's price, so without this the two are identical by the time
    # the QA pass runs and the comparison would always come out at zero.
    # Underscore-prefixed, following the pipeline's convention for values that
    # travel with a product dict but are not columns.
    if invoice_cost is not None:
        product._invoice_cost_price_eur = invoice_cost
    if order_cost is not None:
        product._order_confirmation_wholesale_price = order_cost

    deviation = _cost_deviates(invoice_cost, order_cost)
    if deviation is not None and deviation > COST_TOLERANCE:
        outcome.warnings.append({
            "level": "warning",
            "code": "cost_mismatch_order_confirmation",
            "field": "cost_price_eur",
            "message": (
                f"Kostpris afviger {deviation * 100:.1f}% mellem faktura "
                f"({invoice_cost:.2f}) og ordrebekræftelse ({order_cost:.2f})"
            ),
        })

    if order_cost is not None:
        _apply("cost_price_eur", order_cost)

    # RRP is why the confirmation is read at all — the invoice never carries one.
    if primary.rrp is not None:
        outcome.data_sources["rrp"] = SOURCE_ORDER_CONFIRMATION

    # ── Images and descriptions stay with the scraper ──
    if getattr(product, "images", None):
        outcome.data_sources.setdefault("images", SOURCE_WEB)
    if getattr(product, "description_da", None):
        outcome.data_sources.setdefault("description_da", SOURCE_WEB)

    # ── Provenance and match bookkeeping ──
    if match is not None:
        product.order_confirmation_line_id = match.order_line_id
        product.match_confidence = match.confidence
        product.match_method = match.match_method

    existing_sources = dict(getattr(product, "data_sources", None) or {})
    existing_sources.update(outcome.data_sources)
    product.data_sources = existing_sources

    if outcome.warnings:
        existing_warnings = list(getattr(product, "qa_warnings", None) or [])
        existing_warnings.extend(outcome.warnings)
        product.qa_warnings = existing_warnings

    return outcome


def group_lines_by_match(
    matches: list[MatchResult], order_lines: list
) -> dict[uuid.UUID, list]:
    """
    Expand each match into every confirmation line for that product.

    A match points at one line, but a product spans one line per size. Lines are
    grouped by normalised style plus colour so the whole size run comes back.
    """
    by_id = {getattr(line, "id", None): line for line in order_lines}

    def _group_key(line) -> tuple[str, str]:
        style = normalize_identifier(
            getattr(line, "style_number", None) or getattr(line, "sku", None)
        )
        colour = (getattr(line, "color_code", None) or getattr(line, "color_name", None) or "")
        return style, colour.strip().upper()

    grouped: dict[tuple[str, str], list] = {}
    for line in order_lines:
        grouped.setdefault(_group_key(line), []).append(line)

    result: dict[uuid.UUID, list] = {}
    for match in matches:
        matched_line = by_id.get(match.order_line_id)
        if matched_line is None:
            continue
        result[match.product_id] = grouped.get(_group_key(matched_line), [matched_line])
    return result
