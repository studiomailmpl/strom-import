"""
Unit tests for order confirmation parsing.

Spreadsheets are built in-memory; the Claude Vision path is faked. No network,
no database, no credentials.
"""

import io
import types

import pytest
from openpyxl import Workbook

from app.services import order_confirmation_parser as ocp


# ═══════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════

def make_xlsx(rows: list[list]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_csv(rows: list[list], delimiter: str = ",") -> bytes:
    lines = [delimiter.join("" if c is None else str(c) for c in row) for row in rows]
    return "\n".join(lines).encode("utf-8")


STANDARD_HEADER = [
    "Style No", "Description", "Colour Code", "Colour", "Size",
    "Qty", "Wholesale Price", "RRP", "EAN",
]


# ═══════════════════════════════════════════════
# Number parsing
# ═══════════════════════════════════════════════

class TestParseNumber:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("48,00", 48.00),          # European decimal comma
            ("1.234,56", 1234.56),     # European thousands + decimal
            ("1,234.56", 1234.56),     # US thousands + decimal
            ("48.00", 48.00),
            ("€ 48,00", 48.00),        # currency symbol
            ("EUR 129.00", 129.00),    # currency code
            ("129", 129.0),
            (48.5, 48.5),
            (48, 48.0),
            ("-12,50", -12.50),
        ],
    )
    def test_parses_the_forms_confirmations_actually_use(self, raw, expected):
        assert ocp.parse_number(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", [None, "", "   ", "n/a", "-", "TOTAL"])
    def test_returns_none_rather_than_guessing(self, raw):
        assert ocp.parse_number(raw) is None

    def test_booleans_are_not_treated_as_numbers(self):
        assert ocp.parse_number(True) is None

    def test_quantity_truncates_to_int(self):
        assert ocp.parse_quantity("4") == 4
        assert ocp.parse_quantity("4,0") == 4
        assert ocp.parse_quantity(None) == 0
        assert ocp.parse_quantity("none") == 0


# ═══════════════════════════════════════════════
# Column mapping
# ═══════════════════════════════════════════════

class TestColumnMapping:
    def test_rrp_and_wholesale_are_kept_apart(self):
        """The whole point of reading confirmations — these must not collapse."""
        mapping = ocp.map_header_row(["Wholesale Price", "RRP"])
        assert mapping == {0: "wholesale_price", 1: "rrp"}

    @pytest.mark.parametrize(
        "header", ["RRP", "SRP", "MSRP", "Retail Price", "Consumer Price",
                   "Vejl. udsalg", "Udsalgspris"],
    )
    def test_retail_price_synonyms(self, header):
        assert ocp.map_header_row([header]) == {0: "rrp"}

    @pytest.mark.parametrize(
        "header", ["Wholesale", "WHSL", "WSP", "Cost", "Unit Price",
                   "Net Price", "Indkøbspris"],
    )
    def test_wholesale_price_synonyms(self, header):
        assert ocp.map_header_row([header]) == {0: "wholesale_price"}

    def test_colour_code_is_claimed_before_colour_name(self):
        mapping = ocp.map_header_row(["Colour Code", "Colour"])
        assert mapping == {0: "color_code", 1: "color_name"}

    def test_a_field_is_only_claimed_once(self):
        """Two 'price'-ish columns must not both become wholesale_price."""
        mapping = ocp.map_header_row(["Unit Price", "Net Price"])
        assert list(mapping.values()) == ["wholesale_price"]

    def test_unknown_columns_are_ignored(self):
        mapping = ocp.map_header_row(["Style No", "Delivery Window", "Notes"])
        assert mapping == {0: "style_number"}

    def test_danish_headers(self):
        mapping = ocp.map_header_row(["Varenummer", "Farve", "Størrelse", "Antal", "Udsalgspris"])
        assert mapping == {
            0: "style_number", 1: "color_name", 2: "size",
            3: "quantity", 4: "rrp",
        }


# ═══════════════════════════════════════════════
# XLSX
# ═══════════════════════════════════════════════

class TestParseXlsx:
    def test_reads_lines_including_rrp(self):
        data = make_xlsx([
            STANDARD_HEADER,
            ["I036159", "Mello Knit Shirt", "3ONXX", "Dark Navy", "M", 4, "82,44", "199,00", "4058459812345"],
            ["I036159", "Mello Knit Shirt", "3ONXX", "Dark Navy", "L", 2, "82,44", "199,00", "4058459812352"],
        ])
        result = ocp.parse_xlsx(data)

        assert result.source == "xlsx"
        assert len(result.lines) == 2
        first = result.lines[0]
        assert first.style_number == "I036159"
        assert first.product_name == "Mello Knit Shirt"
        assert first.color_code == "3ONXX"
        assert first.color_name == "Dark Navy"
        assert first.size == "M"
        assert first.quantity == 4
        assert first.wholesale_price == pytest.approx(82.44)
        assert first.rrp == pytest.approx(199.00)
        assert first.ean == "4058459812345"

    def test_header_below_a_logo_and_address_block(self):
        """Confirmations rarely start the table on row 1."""
        data = make_xlsx([
            ["CARHARTT WIP"],
            ["Order confirmation"],
            [],
            STANDARD_HEADER,
            ["I036159", "Shirt", "3ONXX", "Navy", "M", 4, "82,44", "199,00", ""],
        ])
        result = ocp.parse_xlsx(data)
        assert len(result.lines) == 1
        assert result.lines[0].rrp == pytest.approx(199.00)

    def test_total_and_note_rows_are_skipped(self):
        data = make_xlsx([
            STANDARD_HEADER,
            ["I036159", "Shirt", "3ONXX", "Navy", "M", 4, "82,44", "199,00", ""],
            [None, None, None, None, None, None, None, None, None],
            [None, None, None, None, "TOTAL", 4, None, None, None],
        ])
        result = ocp.parse_xlsx(data)
        assert len(result.lines) == 1

    def test_order_number_season_and_currency_read_from_the_header_block(self):
        data = make_xlsx([
            ["Carhartt WIP"],
            ["Order No: 25VA051691", "Season: AW26", "Currency: EUR"],
            STANDARD_HEADER,
            ["I036159", "Shirt", "3ONXX", "Navy", "M", 4, "82,44", "199,00", ""],
        ])
        result = ocp.parse_xlsx(data)
        assert result.order_number == "25VA051691"
        assert result.season == "AW26"
        assert result.currency == "EUR"

    def test_no_recognisable_header_yields_no_lines_rather_than_garbage(self):
        data = make_xlsx([["a", "b"], ["1", "2"]])
        result = ocp.parse_xlsx(data)
        assert result.lines == []
        assert result.source == "xlsx"

    def test_numeric_cells_do_not_gain_a_decimal_suffix(self):
        """openpyxl hands back 4058459812345.0 — the EAN must not become '...0'."""
        data = make_xlsx([
            STANDARD_HEADER,
            ["I036159", "Shirt", "3ONXX", "Navy", "M", 4, 82.44, 199.0, 4058459812345],
        ])
        result = ocp.parse_xlsx(data)
        assert result.lines[0].ean == "4058459812345"


# ═══════════════════════════════════════════════
# CSV
# ═══════════════════════════════════════════════

class TestParseCsv:
    def test_comma_delimited(self):
        data = make_csv([
            ["Style No", "Description", "Size", "Qty", "Wholesale", "RRP"],
            ["ABC123", "Wool Coat", "M", "3", "150.00", "449.00"],
        ])
        result = ocp.parse_csv(data)
        assert result.source == "csv"
        assert len(result.lines) == 1
        assert result.lines[0].rrp == pytest.approx(449.00)
        assert result.lines[0].wholesale_price == pytest.approx(150.00)

    def test_semicolon_delimited_european_export(self):
        data = make_csv(
            [
                ["Style No", "Description", "Size", "Qty", "Wholesale", "RRP"],
                ["ABC123", "Wool Coat", "M", "3", "150,00", "449,00"],
            ],
            delimiter=";",
        )
        result = ocp.parse_csv(data)
        assert len(result.lines) == 1
        assert result.lines[0].rrp == pytest.approx(449.00)

    def test_utf8_bom_is_stripped_from_the_first_header(self):
        data = b"\xef\xbb\xbf" + make_csv([
            ["Style No", "Size", "Qty", "RRP"],
            ["ABC123", "M", "3", "449.00"],
        ])
        result = ocp.parse_csv(data)
        assert len(result.lines) == 1
        assert result.lines[0].style_number == "ABC123"


# ═══════════════════════════════════════════════
# Dispatch
# ═══════════════════════════════════════════════

class TestDispatch:
    async def test_xlsx_by_mime_type(self):
        data = make_xlsx([STANDARD_HEADER, ["A", "n", "c", "col", "M", 1, "1,00", "2,00", ""]])
        result = await ocp.parse_order_confirmation(data, "order.bin", ocp.MIME_XLSX)
        assert result.source == "xlsx"

    async def test_xlsx_by_extension_when_mime_is_vague(self):
        data = make_xlsx([STANDARD_HEADER, ["A", "n", "c", "col", "M", 1, "1,00", "2,00", ""]])
        result = await ocp.parse_order_confirmation(
            data, "order.xlsx", "application/octet-stream"
        )
        assert result.source == "xlsx"

    async def test_csv_by_extension(self):
        data = make_csv([["Style No", "Size", "Qty", "RRP"], ["A", "M", "1", "2.00"]])
        result = await ocp.parse_order_confirmation(data, "order.csv", "text/plain")
        assert result.source == "csv"

    async def test_unsupported_type_is_rejected(self):
        with pytest.raises(ocp.UnsupportedFileType):
            await ocp.parse_order_confirmation(b"x", "order.docx", "application/msword")

    async def test_pdf_without_an_api_key_fails_loudly(self):
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            await ocp.parse_order_confirmation(b"%PDF-", "order.pdf", ocp.MIME_PDF)

    def test_google_doc_is_treated_as_pdf(self):
        """download_file exports Workspace files to PDF, so the bytes are a PDF."""
        assert ocp._detect_kind("Order", "application/vnd.google-apps.document") == "pdf"


# ═══════════════════════════════════════════════
# Claude Vision path
# ═══════════════════════════════════════════════

class _ToolUseBlock:
    type = "tool_use"
    name = "submit_order_confirmation"

    def __init__(self, payload):
        self.input = payload


class _Message:
    def __init__(self, blocks):
        self.content = blocks


def fake_anthropic(payload, capture: dict):
    class _Messages:
        async def create(self, **kwargs):
            capture.update(kwargs)
            return _Message([_ToolUseBlock(payload)])

    class _Client:
        def __init__(self, *a, **kw):
            self.messages = _Messages()

    return types.SimpleNamespace(AsyncAnthropic=_Client)


AI_PAYLOAD = {
    "order_number": "25VA051691",
    "vendor": "Carhartt WIP",
    "season": "AW26",
    "currency": "eur",
    "lines": [
        {
            "style_number": "I036159", "sku": "", "ean": "4058459812345",
            "product_name": "Mello Knit Shirt", "color_code": "3ONXX",
            "color_name": "Dark Navy", "size": "M", "quantity": 4,
            "wholesale_price": 82.44, "rrp": 199.0,
        },
        {  # no identity at all — must be dropped
            "style_number": "", "sku": "", "product_name": "",
            "size": "L", "quantity": 2,
        },
    ],
}


class TestVisionPath:
    @pytest.fixture(autouse=True)
    def _stub_pdf(self, monkeypatch):
        monkeypatch.setattr(ocp, "extract_pdf_text", lambda b: "faktura tekst")
        monkeypatch.setattr(ocp, "extract_pdf_pages_as_images", lambda b: ["b64page"])

    async def test_extracts_header_and_lines(self, monkeypatch):
        capture: dict = {}
        monkeypatch.setattr(ocp, "anthropic", fake_anthropic(AI_PAYLOAD, capture))

        result = await ocp.parse_pdf_with_ai(b"%PDF-", api_key="k")

        assert result.source == "ai_vision"
        assert result.order_number == "25VA051691"
        assert result.vendor == "Carhartt WIP"
        assert result.season == "AW26"
        assert result.currency == "EUR", "currency is normalised to upper case"
        assert len(result.lines) == 1, "the line with no identity is dropped"
        assert result.lines[0].rrp == pytest.approx(199.0)
        assert result.lines[0].wholesale_price == pytest.approx(82.44)

    async def test_schema_and_prompt_cover_the_fields_that_matter(self, monkeypatch):
        capture: dict = {}
        monkeypatch.setattr(ocp, "anthropic", fake_anthropic(AI_PAYLOAD, capture))
        await ocp.parse_pdf_with_ai(b"%PDF-", api_key="k")

        schema = capture["tools"][0]["input_schema"]
        assert capture["tool_choice"]["name"] == "submit_order_confirmation"
        for field in ("order_number", "vendor", "season", "currency", "lines"):
            assert field in schema["properties"]

        line_props = schema["properties"]["lines"]["items"]["properties"]
        for field in ("style_number", "sku", "ean", "product_name", "color_code",
                      "color_name", "size", "quantity", "wholesale_price", "rrp"):
            assert field in line_props, f"{field} missing from the line schema"

        system = capture["system"]
        assert "RRP" in system
        assert "wholesale_price" in system
        assert "Gæt ALDRIG en RRP" in system, "the prompt must forbid deriving RRP"

    async def test_both_the_page_image_and_the_text_are_sent(self, monkeypatch):
        capture: dict = {}
        monkeypatch.setattr(ocp, "anthropic", fake_anthropic(AI_PAYLOAD, capture))
        await ocp.parse_pdf_with_ai(b"%PDF-", api_key="k")

        parts = capture["messages"][0]["content"]
        assert parts[0]["type"] == "image"
        assert parts[0]["source"]["data"] == "b64page"
        assert "faktura tekst" in parts[-1]["text"]

    async def test_european_numbers_from_the_model_are_coerced(self, monkeypatch):
        payload = {
            "lines": [{
                "style_number": "A", "size": "M", "quantity": "4",
                "wholesale_price": "82,44", "rrp": "1.199,00",
            }]
        }
        monkeypatch.setattr(ocp, "anthropic", fake_anthropic(payload, {}))
        result = await ocp.parse_pdf_with_ai(b"%PDF-", api_key="k")
        assert result.lines[0].quantity == 4
        assert result.lines[0].wholesale_price == pytest.approx(82.44)
        assert result.lines[0].rrp == pytest.approx(1199.00)

    async def test_a_reply_with_no_tool_use_does_not_raise(self, monkeypatch):
        class _TextBlock:
            type = "text"
            text = "beklager"

        class _Messages:
            async def create(self, **kwargs):
                return _Message([_TextBlock()])

        class _Client:
            def __init__(self, *a, **kw):
                self.messages = _Messages()

        monkeypatch.setattr(ocp, "anthropic", types.SimpleNamespace(AsyncAnthropic=_Client))
        result = await ocp.parse_pdf_with_ai(b"%PDF-", api_key="k")
        assert result.lines == []
        assert result.source == "ai_vision"


# ═══════════════════════════════════════════════
# Serialisation
# ═══════════════════════════════════════════════

def test_parsed_confirmation_serialises_for_an_api_response():
    parsed = ocp.ParsedOrderConfirmation(
        order_number="1", vendor="V", season="AW26", currency="EUR", source="xlsx",
        lines=[ocp.ParsedOrderConfirmationLine(style_number="A", size="M", quantity=2, rrp=99.0)],
    )
    payload = parsed.as_dict()
    assert payload["order_number"] == "1"
    assert payload["source"] == "xlsx"
    assert payload["lines"][0]["rrp"] == 99.0
    assert payload["lines"][0]["style_number"] == "A"
