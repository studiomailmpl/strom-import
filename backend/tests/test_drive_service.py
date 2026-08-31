"""
Unit tests for the Google Drive order-confirmation search and download.

The Drive API is faked end to end — no network, no database, no credentials.
FakeDriveClient records the queries it is given so the tests can assert on the
exact query strings the service builds.
"""

import uuid

import pytest

from app.services import drive_service as ds


# ═══════════════════════════════════════════════
# Fake Drive API
# ═══════════════════════════════════════════════

class _Executable:
    """Stands in for a googleapiclient request object."""

    def __init__(self, result, error: Exception | None = None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


class FakeFiles:
    def __init__(
        self,
        list_results=None,
        metadata=None,
        media_bytes=b"",
        export_bytes=b"",
    ):
        # list_results: a list of responses (or Exceptions) returned in order,
        # one per files().list() call.
        self._list_results = list(list_results or [])
        self._metadata = metadata or {}
        self._media_bytes = media_bytes
        self._export_bytes = export_bytes
        self.list_calls: list[dict] = []
        self.get_media_calls: list[dict] = []
        self.export_calls: list[dict] = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if not self._list_results:
            return _Executable({"files": []})
        nxt = self._list_results.pop(0)
        if isinstance(nxt, Exception):
            return _Executable(None, error=nxt)
        return _Executable(nxt)

    def get(self, **kwargs):
        return _Executable(self._metadata)

    def get_media(self, **kwargs):
        self.get_media_calls.append(kwargs)
        return _Executable(self._media_bytes)

    def export_media(self, **kwargs):
        self.export_calls.append(kwargs)
        return _Executable(self._export_bytes)


class FakeDriveClient:
    def __init__(self, files: FakeFiles):
        self._files = files

    def files(self):
        return self._files


def drive_file(file_id, name="file.pdf", mime=ds.MIME_PDF, modified="2026-05-01T10:00:00.000Z"):
    return {"id": file_id, "name": name, "mimeType": mime, "modifiedTime": modified}


# ═══════════════════════════════════════════════
# Season variants
# ═══════════════════════════════════════════════

class TestSeasonSearchVariants:
    def test_av26_also_searches_aw26_and_fw26(self):
        """The mapping named in the spec: AV26 must reach AW26 and FW26."""
        variants = ds.season_search_variants("AV26")
        assert variants[0] == "AV26", "the season as given must be searched first"
        assert "AW26" in variants
        assert "FW26" in variants

    def test_ss27_also_searches_spelled_out_forms(self):
        variants = ds.season_search_variants("SS27")
        assert variants[0] == "SS27"
        assert "S/S" in variants
        assert "Spring Summer" in variants

    @pytest.mark.parametrize(
        "season,expected",
        [
            ("Pre-Spring 2027", "PS27"),
            ("Pre-Fall 2026", "PF26"),
            ("Fall/Winter 2026", "AW26"),
            ("E26", "SS26"),
        ],
    )
    def test_written_out_seasons_expand_to_their_code(self, season, expected):
        assert expected in ds.season_search_variants(season)

    def test_pre_spring_does_not_collapse_into_spring(self):
        """"Pre-Spring" must map to PS, never fall through to the SS rule."""
        variants = ds.season_search_variants("Pre-Spring 2027")
        assert "PS27" in variants
        assert "SS27" not in variants

    def test_unknown_season_is_kept_rather_than_dropped(self):
        assert ds.season_search_variants("BRANDCODE9") == ["BRANDCODE9"]

    def test_bare_codes_need_a_year_to_be_searched(self):
        """"SS" alone would match half the drive, so only word forms are used."""
        variants = ds.season_search_variants("SS")
        assert variants[0] == "SS"
        assert "Spring Summer" in variants
        assert not any(v.startswith("PE") for v in variants)

    def test_empty_season_yields_nothing(self):
        assert ds.season_search_variants("") == []
        assert ds.season_search_variants("   ") == []


# ═══════════════════════════════════════════════
# Query building
# ═══════════════════════════════════════════════

class TestQueryEscaping:
    def test_apostrophe_is_escaped(self):
        """Without escaping, "Levi's" would close the quote and break the query."""
        assert ds._escape_query_value("Levi's") == "Levi\\'s"

    def test_backslash_is_escaped(self):
        assert ds._escape_query_value("a\\b") == "a\\\\b"

    def test_vendor_with_apostrophe_produces_a_balanced_query(self):
        queries = ds.build_ranked_queries(vendor_name="Levi's")
        query = queries[0][2]
        assert "Levi\\'s" in query
        # Every remaining unescaped quote must pair up.
        assert query.replace("\\'", "").count("'") % 2 == 0


class TestBuildRankedQueries:
    def test_queries_are_ordered_most_specific_first(self):
        queries = ds.build_ranked_queries(
            vendor_name="Carhartt WIP",
            order_number="25VA051691",
            season="AW26",
            skus=["I036159"],
        )
        scores = [score for score, _, _ in queries]
        assert scores == [
            ds.SCORE_ORDER_NUMBER,
            ds.SCORE_VENDOR_SEASON,
            ds.SCORE_SKU,
            ds.SCORE_VENDOR,
        ]
        assert scores == sorted(scores, reverse=True)

    def test_order_number_uses_full_text_search(self):
        queries = ds.build_ranked_queries(vendor_name="V", order_number="25VA051691")
        score, matched_by, query = queries[0]
        assert score == ds.SCORE_ORDER_NUMBER
        assert matched_by == "order_number"
        assert "fullText contains '25VA051691'" in query

    def test_vendor_season_query_ands_vendor_with_any_season_variant(self):
        queries = ds.build_ranked_queries(vendor_name="Carhartt", season="AV26")
        query = next(q for s, _, q in queries if s == ds.SCORE_VENDOR_SEASON)
        assert "name contains 'Carhartt'" in query
        assert "name contains 'AW26'" in query
        assert "name contains 'FW26'" in query
        assert " or " in query

    def test_each_sku_gets_its_own_full_text_query(self):
        queries = ds.build_ranked_queries(
            vendor_name="V", skus=["SKU-A", "SKU-B"]
        )
        sku_queries = [q for s, _, q in queries if s == ds.SCORE_SKU]
        assert len(sku_queries) == 2
        assert "fullText contains 'SKU-A'" in sku_queries[0]
        assert "fullText contains 'SKU-B'" in sku_queries[1]

    def test_sku_queries_are_capped(self):
        """A 200-line invoice must not fire 200 Drive calls."""
        queries = ds.build_ranked_queries(
            vendor_name="V", skus=[f"SKU{i}" for i in range(50)], max_skus=10
        )
        assert sum(1 for s, _, _ in queries if s == ds.SCORE_SKU) == 10

    def test_vendor_only_is_the_broadest_fallback(self):
        queries = ds.build_ranked_queries(vendor_name="Marni")
        assert len(queries) == 1
        score, matched_by, query = queries[0]
        assert score == ds.SCORE_VENDOR
        assert matched_by == "vendor"
        assert "name contains 'Marni'" in query

    def test_no_vendor_and_no_signals_yields_no_queries(self):
        assert ds.build_ranked_queries(vendor_name="") == []
        assert ds.build_ranked_queries(vendor_name="   ") == []

    def test_order_number_is_searched_even_without_a_vendor(self):
        queries = ds.build_ranked_queries(vendor_name="", order_number="12345")
        assert len(queries) == 1
        assert queries[0][0] == ds.SCORE_ORDER_NUMBER

    def test_blank_skus_are_skipped(self):
        queries = ds.build_ranked_queries(vendor_name="V", skus=["", "   ", "REAL"])
        sku_queries = [q for s, _, q in queries if s == ds.SCORE_SKU]
        assert len(sku_queries) == 1
        assert "REAL" in sku_queries[0]

    def test_every_query_excludes_trashed_files_and_filters_mime_types(self):
        queries = ds.build_ranked_queries(
            vendor_name="V", order_number="1", season="AW26", skus=["S"]
        )
        for _, _, query in queries:
            assert "trashed = false" in query
            assert f"mimeType = '{ds.MIME_PDF}'" in query
            assert f"mimeType = '{ds.MIME_XLSX}'" in query
            assert f"mimeType = '{ds.MIME_CSV}'" in query
            assert f"mimeType = '{ds.MIME_GOOGLE_DOC}'" in query

    def test_root_folder_restricts_every_query(self):
        queries = ds.build_ranked_queries(
            vendor_name="V", order_number="1", root_folder_id="FOLDER123"
        )
        for _, _, query in queries:
            assert "'FOLDER123' in parents" in query

    def test_no_root_folder_means_no_parent_clause(self):
        queries = ds.build_ranked_queries(vendor_name="V", root_folder_id=None)
        assert "in parents" not in queries[0][2]


# ═══════════════════════════════════════════════
# Running and ranking
# ═══════════════════════════════════════════════

class TestRunRankedQueries:
    def test_returns_candidates_with_their_score(self):
        files = FakeFiles(list_results=[{"files": [drive_file("f1", "Carhartt AW26.pdf")]}])
        results = ds._run_ranked_queries(
            FakeDriveClient(files), [(ds.SCORE_ORDER_NUMBER, "order_number", "q")]
        )
        assert len(results) == 1
        assert results[0].file_id == "f1"
        assert results[0].name == "Carhartt AW26.pdf"
        assert results[0].mime_type == ds.MIME_PDF
        assert results[0].modified_time == "2026-05-01T10:00:00.000Z"
        assert results[0].score == ds.SCORE_ORDER_NUMBER
        assert results[0].matched_by == "order_number"

    def test_a_file_matched_twice_keeps_its_best_score(self):
        """The same file found by order number and by vendor scores 100, not 40."""
        files = FakeFiles(list_results=[
            {"files": [drive_file("f1")]},   # score 100
            {"files": [drive_file("f1")]},   # score 40, same file
        ])
        results = ds._run_ranked_queries(FakeDriveClient(files), [
            (ds.SCORE_ORDER_NUMBER, "order_number", "q1"),
            (ds.SCORE_VENDOR, "vendor", "q2"),
        ])
        assert len(results) == 1
        assert results[0].score == ds.SCORE_ORDER_NUMBER
        assert results[0].matched_by == "order_number"

    def test_results_are_sorted_by_score_then_newest_first(self):
        files = FakeFiles(list_results=[
            {"files": [drive_file("old", modified="2026-01-01T00:00:00.000Z"),
                       drive_file("new", modified="2026-06-01T00:00:00.000Z")]},
            {"files": [drive_file("low")]},
        ])
        results = ds._run_ranked_queries(FakeDriveClient(files), [
            (ds.SCORE_VENDOR_SEASON, "vendor+season", "q1"),
            (ds.SCORE_VENDOR, "vendor", "q2"),
        ])
        assert [c.file_id for c in results] == ["new", "old", "low"]

    def test_at_most_five_candidates_are_returned(self):
        files = FakeFiles(list_results=[
            {"files": [drive_file(f"f{i}") for i in range(12)]},
        ])
        results = ds._run_ranked_queries(
            FakeDriveClient(files), [(ds.SCORE_VENDOR, "vendor", "q")]
        )
        assert len(results) == ds.MAX_CANDIDATES == 5

    def test_search_stops_once_enough_candidates_are_found(self):
        """Scores only descend, so later queries cannot change the top five."""
        files = FakeFiles(list_results=[
            {"files": [drive_file(f"f{i}") for i in range(5)]},
            {"files": [drive_file("should-not-be-fetched")]},
        ])
        ds._run_ranked_queries(FakeDriveClient(files), [
            (ds.SCORE_ORDER_NUMBER, "order_number", "q1"),
            (ds.SCORE_VENDOR, "vendor", "q2"),
        ])
        assert len(files.list_calls) == 1

    def test_a_failing_query_does_not_abort_the_search(self):
        files = FakeFiles(list_results=[
            RuntimeError("Drive 500"),
            {"files": [drive_file("f2")]},
        ])
        results = ds._run_ranked_queries(FakeDriveClient(files), [
            (ds.SCORE_ORDER_NUMBER, "order_number", "q1"),
            (ds.SCORE_VENDOR, "vendor", "q2"),
        ])
        assert [c.file_id for c in results] == ["f2"]

    def test_files_without_an_id_are_ignored(self):
        files = FakeFiles(list_results=[{"files": [{"name": "no id"}, drive_file("f1")]}])
        results = ds._run_ranked_queries(
            FakeDriveClient(files), [(ds.SCORE_VENDOR, "vendor", "q")]
        )
        assert [c.file_id for c in results] == ["f1"]

    def test_empty_response_is_handled(self):
        files = FakeFiles(list_results=[{}, {"files": None}])
        results = ds._run_ranked_queries(FakeDriveClient(files), [
            (ds.SCORE_ORDER_NUMBER, "order_number", "q1"),
            (ds.SCORE_VENDOR, "vendor", "q2"),
        ])
        assert results == []


# ═══════════════════════════════════════════════
# search_order_confirmations
# ═══════════════════════════════════════════════

class _FakeConnection:
    def __init__(self, root_folder_id=None):
        self.root_folder_id = root_folder_id


@pytest.fixture
def org_id():
    return uuid.uuid4()


class TestSearchOrderConfirmations:
    async def test_returns_empty_when_drive_is_not_connected(self, monkeypatch, org_id):
        monkeypatch.setattr(ds, "get_valid_access_token", _async_return(None))
        result = await ds.search_order_confirmations(None, org_id, vendor_name="Marni")
        assert result == []

    async def test_returns_ranked_candidates(self, monkeypatch, org_id):
        files = FakeFiles(list_results=[
            {"files": [drive_file("f1", "Carhartt AW26 order.pdf")]},
        ])
        _patch_connected(monkeypatch, files, connection=_FakeConnection())

        result = await ds.search_order_confirmations(
            None, org_id, vendor_name="Carhartt", order_number="25VA051691"
        )
        assert [c.file_id for c in result] == ["f1"]
        assert result[0].score == ds.SCORE_ORDER_NUMBER

    async def test_root_folder_from_the_connection_is_applied(self, monkeypatch, org_id):
        files = FakeFiles(list_results=[{"files": []}])
        _patch_connected(monkeypatch, files, connection=_FakeConnection("ROOT42"))

        await ds.search_order_confirmations(None, org_id, vendor_name="Marni")
        assert "'ROOT42' in parents" in files.list_calls[0]["q"]

    async def test_no_signals_makes_no_api_calls(self, monkeypatch, org_id):
        files = FakeFiles(list_results=[{"files": []}])
        _patch_connected(monkeypatch, files, connection=_FakeConnection())

        result = await ds.search_order_confirmations(None, org_id, vendor_name="")
        assert result == []
        assert files.list_calls == []

    async def test_candidate_serialises_for_an_api_response(self, monkeypatch, org_id):
        files = FakeFiles(list_results=[{"files": [drive_file("f1", "x.pdf")]}])
        _patch_connected(monkeypatch, files, connection=_FakeConnection())

        result = await ds.search_order_confirmations(None, org_id, vendor_name="V")
        payload = result[0].as_dict()
        assert payload["file_id"] == "f1"
        assert payload["name"] == "x.pdf"
        assert payload["mime_type"] == ds.MIME_PDF
        assert payload["modified_time"] == "2026-05-01T10:00:00.000Z"
        assert payload["score"] == ds.SCORE_VENDOR


# ═══════════════════════════════════════════════
# download_file
# ═══════════════════════════════════════════════

class TestDownloadFile:
    async def test_returns_none_when_not_connected(self, monkeypatch, org_id):
        monkeypatch.setattr(ds, "get_valid_access_token", _async_return(None))
        assert await ds.download_file(None, org_id, "f1") is None

    async def test_native_file_is_downloaded_as_stored(self, monkeypatch, org_id):
        files = FakeFiles(
            metadata={"id": "f1", "name": "order.pdf", "mimeType": ds.MIME_PDF},
            media_bytes=b"%PDF-1.7 native bytes",
        )
        _patch_connected(monkeypatch, files)

        data = await ds.download_file(None, org_id, "f1")
        assert data == b"%PDF-1.7 native bytes"
        assert files.get_media_calls == [{"fileId": "f1"}]
        assert files.export_calls == [], "a native PDF must not be exported"

    async def test_xlsx_uses_get_media_too(self, monkeypatch, org_id):
        files = FakeFiles(
            metadata={"id": "f1", "name": "order.xlsx", "mimeType": ds.MIME_XLSX},
            media_bytes=b"PK\x03\x04xlsx",
        )
        _patch_connected(monkeypatch, files)

        assert await ds.download_file(None, org_id, "f1") == b"PK\x03\x04xlsx"
        assert len(files.get_media_calls) == 1

    async def test_google_doc_is_exported_to_pdf(self, monkeypatch, org_id):
        files = FakeFiles(
            metadata={"id": "f1", "name": "Order", "mimeType": ds.MIME_GOOGLE_DOC},
            export_bytes=b"%PDF-1.7 exported",
        )
        _patch_connected(monkeypatch, files)

        data = await ds.download_file(None, org_id, "f1")
        assert data == b"%PDF-1.7 exported"
        assert files.export_calls == [{"fileId": "f1", "mimeType": ds.MIME_PDF}]
        assert files.get_media_calls == [], "a Google Doc has no bytes to fetch directly"

    async def test_drive_errors_propagate(self, monkeypatch, org_id):
        class _Boom(FakeFiles):
            def get(self, **kwargs):
                raise RuntimeError("Drive 403")

        _patch_connected(monkeypatch, _Boom())
        with pytest.raises(RuntimeError, match="Drive 403"):
            await ds.download_file(None, org_id, "f1")


# ═══════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════

def _async_return(value):
    async def _fn(*args, **kwargs):
        return value
    return _fn


def _patch_connected(monkeypatch, files: FakeFiles, connection=None):
    """Wire the module up as if Drive were connected, backed by a fake client."""
    monkeypatch.setattr(ds, "get_valid_access_token", _async_return("token"))
    monkeypatch.setattr(ds, "get_drive_connection", _async_return(connection))
    monkeypatch.setattr(ds, "build_drive_client", lambda token: FakeDriveClient(files))


# ═══════════════════════════════════════════════
# Indexing
# ═══════════════════════════════════════════════

class TestLooksLikeOrderConfirmation:
    @pytest.mark.parametrize(
        "name",
        [
            "Order Confirmation 25VA051691.pdf",
            "order_confirmation_AW26.xlsx",
            "Ordrebekræftelse AW26.pdf",
            "Auftragsbestätigung 123.pdf",
            "confirmation de commande.pdf",
            "SALES ORDER 998.csv",
        ],
    )
    def test_confirmation_keywords_in_several_languages(self, name):
        assert ds.looks_like_order_confirmation(name)

    def test_a_known_vendor_name_counts(self):
        """Brands often name the file after themselves and the season."""
        assert ds.looks_like_order_confirmation(
            "Carhartt WIP AW26.xlsx", vendor_names=["Carhartt WIP"]
        )

    def test_vendor_matching_is_case_insensitive(self):
        assert ds.looks_like_order_confirmation("marni ss27.pdf", vendor_names=["Marni"])

    def test_very_short_vendor_names_are_ignored(self):
        """A two-letter brand would match almost every filename."""
        assert not ds.looks_like_order_confirmation("AB Invoice.pdf", vendor_names=["AB"])

    @pytest.mark.parametrize(
        "name", ["Faktura 12345.pdf", "Price list SS27.xlsx", "Lookbook.pdf", ""],
    )
    def test_unrelated_files_are_rejected(self, name):
        assert not ds.looks_like_order_confirmation(name, vendor_names=["Marni"])


class TestBuildIndexQueries:
    def test_covers_every_keyword_across_batches(self):
        queries = ds.build_index_queries()
        combined = " ".join(queries)
        for hint in ds.ORDER_CONFIRMATION_NAME_HINTS:
            # Hints go through the same escaping as any other value —
            # "conferma d'ordine" would otherwise break the query.
            assert f"name contains '{ds._escape_query_value(hint)}'" in combined

    def test_terms_are_batched_rather_than_one_query_each(self):
        queries = ds.build_index_queries(terms_per_query=5)
        assert len(queries) < len(ds.ORDER_CONFIRMATION_NAME_HINTS)
        assert len(queries) >= 2

    def test_vendor_names_are_included(self):
        combined = " ".join(ds.build_index_queries(vendor_names=["Carhartt WIP"]))
        assert "name contains 'Carhartt WIP'" in combined

    def test_short_vendor_names_are_dropped(self):
        combined = " ".join(ds.build_index_queries(vendor_names=["AB", "Marni"]))
        assert "name contains 'AB'" not in combined
        assert "name contains 'Marni'" in combined

    def test_vendor_count_is_capped(self):
        vendors = [f"Brand{i:03d}" for i in range(200)]
        combined = " ".join(ds.build_index_queries(vendor_names=vendors, max_vendors=10))
        assert "name contains 'Brand009'" in combined
        assert "name contains 'Brand010'" not in combined

    def test_root_folder_and_shared_constraints_apply_to_every_query(self):
        queries = ds.build_index_queries(root_folder_id="ROOT42")
        for query in queries:
            assert "'ROOT42' in parents" in query
            assert "trashed = false" in query
            assert f"mimeType = '{ds.MIME_PDF}'" in query

    def test_apostrophes_in_a_vendor_name_are_escaped(self):
        combined = " ".join(ds.build_index_queries(vendor_names=["Levi's"]))
        assert "Levi\\'s" in combined


class TestRunIndexQueries:
    def test_follows_pagination(self):
        files = FakeFiles(list_results=[
            {"files": [drive_file("f1")], "nextPageToken": "p2"},
            {"files": [drive_file("f2")]},
        ])
        result = ds._run_index_queries(FakeDriveClient(files), ["q"], max_files=100)
        assert {f["id"] for f in result} == {"f1", "f2"}
        assert files.list_calls[1]["pageToken"] == "p2"

    def test_deduplicates_files_seen_by_more_than_one_query(self):
        files = FakeFiles(list_results=[
            {"files": [drive_file("f1")]},
            {"files": [drive_file("f1"), drive_file("f2")]},
        ])
        result = ds._run_index_queries(FakeDriveClient(files), ["q1", "q2"], max_files=100)
        assert len(result) == 2

    def test_stops_at_max_files(self):
        files = FakeFiles(list_results=[
            {"files": [drive_file(f"f{i}") for i in range(10)], "nextPageToken": "p2"},
        ])
        result = ds._run_index_queries(FakeDriveClient(files), ["q"], max_files=3)
        assert len(result) >= 3
        assert len(files.list_calls) == 1, "must not keep paging past the cap"

    def test_a_failing_query_does_not_abort_the_sweep(self):
        files = FakeFiles(list_results=[RuntimeError("Drive 500"), {"files": [drive_file("f2")]}])
        result = ds._run_index_queries(FakeDriveClient(files), ["q1", "q2"], max_files=100)
        assert [f["id"] for f in result] == ["f2"]


class TestListOrderConfirmationCandidates:
    async def test_returns_empty_when_not_connected(self, monkeypatch, org_id):
        monkeypatch.setattr(ds, "get_valid_access_token", _async_return(None))
        assert await ds.list_order_confirmation_candidates(None, org_id) == []

    async def test_filters_out_files_that_only_matched_loosely(self, monkeypatch, org_id):
        """Drive's name search is a loose token match, so re-check locally."""
        files = FakeFiles(list_results=[{"files": [
            drive_file("keep", "Order Confirmation AW26.pdf"),
            drive_file("drop", "Random Lookbook.pdf"),
        ]}])
        _patch_connected(monkeypatch, files, connection=_FakeConnection())

        result = await ds.list_order_confirmation_candidates(None, org_id)
        assert [c.file_id for c in result] == ["keep"]

    async def test_uses_the_configured_root_folder(self, monkeypatch, org_id):
        files = FakeFiles(list_results=[{"files": []}])
        _patch_connected(monkeypatch, files, connection=_FakeConnection("ROOT42"))

        await ds.list_order_confirmation_candidates(None, org_id)
        assert "'ROOT42' in parents" in files.list_calls[0]["q"]

    async def test_candidates_carry_what_the_parser_needs(self, monkeypatch, org_id):
        files = FakeFiles(list_results=[{"files": [
            drive_file("f1", "Order Confirmation.pdf", modified="2026-06-01T00:00:00.000Z")
        ]}])
        _patch_connected(monkeypatch, files, connection=_FakeConnection())

        candidate = (await ds.list_order_confirmation_candidates(None, org_id))[0]
        assert candidate.name == "Order Confirmation.pdf"
        assert candidate.mime_type == ds.MIME_PDF
        assert candidate.modified_time == "2026-06-01T00:00:00.000Z"
        assert candidate.matched_by == "index"
