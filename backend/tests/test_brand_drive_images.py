"""
Unit tests for fetching brand packshots from a Google Drive folder.

The Drive client is faked and the image directory is redirected to a tmp path,
so nothing touches the network or the real upload directory.
"""

import pytest

from app.services import image_service as img


class _Executable:
    def __init__(self, result, error=None):
        self._result, self._error = result, error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


class FakeFiles:
    def __init__(self, listing=None, media=None, media_error=None):
        self._listing = listing if listing is not None else {"files": []}
        self._media = media or {}
        self._media_error = media_error
        self.list_calls: list[dict] = []
        self.media_calls: list[str] = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return _Executable(self._listing)

    def get_media(self, fileId=None, **kwargs):
        self.media_calls.append(fileId)
        if self._media_error:
            return _Executable(None, error=self._media_error)
        return _Executable(self._media.get(fileId, b""))


class FakeClient:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


@pytest.fixture
def image_dir(tmp_path, monkeypatch):
    """Point the service at a throwaway image directory."""
    from app.core.config import Settings, get_settings

    real = get_settings()
    fake = Settings(
        **{
            **real.model_dump(),
            "image_upload_dir": str(tmp_path),
            "public_base_url": "https://api.example.test",
        }
    )
    monkeypatch.setattr(img, "get_settings", lambda: fake)
    return tmp_path


def patch_drive(monkeypatch, files: FakeFiles):
    import app.services.drive_service as ds
    monkeypatch.setattr(ds, "build_drive_client", lambda token: FakeClient(files))


class TestSkuVariants:
    def test_searches_the_code_with_and_without_separators(self):
        assert img._drive_sku_variants("COHBU-M26388") == ["COHBU-M26388", "COHBUM26388"]

    def test_a_code_without_separators_is_not_duplicated(self):
        assert img._drive_sku_variants("I036159") == ["I036159"]

    def test_empty_code_yields_nothing(self):
        assert img._drive_sku_variants("") == []


class TestFetchBrandDriveImages:
    def test_downloads_matching_images_and_returns_public_urls(self, image_dir, monkeypatch):
        files = FakeFiles(
            listing={"files": [
                {"id": "a", "name": "COHBU-M26388_1.jpg", "mimeType": "image/jpeg"},
                {"id": "b", "name": "COHBU-M26388_2.jpg", "mimeType": "image/jpeg"},
            ]},
            media={"a": b"\xff\xd8jpegone", "b": b"\xff\xd8jpegtwo"},
        )
        patch_drive(monkeypatch, files)

        urls = img.fetch_brand_drive_images(
            folder_id="FOLDER1", access_token="tok",
            style_code="COHBU-M26388", vendor="A.P.C.",
        )

        # Served from our own API, not from Drive — Shopify cannot read a Drive
        # URL, and the bytes are behind OAuth.
        assert all(u.startswith("https://api.example.test/api/v1/images/drive/") for u in urls)
        assert len(urls) == 2

        # The URL must match a route that actually exists, or every packshot is
        # a dead link and the product reaches Shopify with no images.
        from app.main import app
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/v1/images/drive/{vendor}/{sku}/{filename}" in paths
        segments = urls[0].split("/api/v1/images/")[1].split("/")
        assert len(segments) == 4 and segments[0] == "drive", (
            "the URL shape must match the route's segment count"
        )

        written = sorted(p.name for p in (image_dir / "drive").rglob("*.jpg"))
        assert written == ["COHBU-M26388_1.jpg", "COHBU-M26388_2.jpg"]
        assert (image_dir / "drive" / "A.P.C." / "COHBU-M26388" / "COHBU-M26388_1.jpg").read_bytes() == b"\xff\xd8jpegone"

    def test_query_is_scoped_to_the_folder_and_to_image_types(self, image_dir, monkeypatch):
        files = FakeFiles()
        patch_drive(monkeypatch, files)

        img.fetch_brand_drive_images(
            folder_id="FOLDER1", access_token="tok", style_code="ABC-123", vendor="V"
        )
        query = files.list_calls[0]["q"]
        assert "'FOLDER1' in parents" in query
        assert "trashed = false" in query
        assert "mimeType = 'image/jpeg'" in query
        assert "name contains 'ABC-123'" in query
        assert "name contains 'ABC123'" in query, "separator-free form must be searched too"

    def test_folder_id_with_an_apostrophe_is_escaped(self, image_dir, monkeypatch):
        files = FakeFiles()
        patch_drive(monkeypatch, files)
        img.fetch_brand_drive_images(
            folder_id="F1", access_token="tok", style_code="Levi's-1", vendor="V"
        )
        assert "Levi\\'s-1" in files.list_calls[0]["q"]

    def test_no_matching_files_returns_nothing(self, image_dir, monkeypatch):
        patch_drive(monkeypatch, FakeFiles(listing={"files": []}))
        assert img.fetch_brand_drive_images(
            folder_id="F1", access_token="tok", style_code="ABC", vendor="V"
        ) == []

    def test_max_images_is_respected(self, image_dir, monkeypatch):
        files = FakeFiles(
            listing={"files": [
                {"id": str(i), "name": f"ABC_{i}.jpg", "mimeType": "image/jpeg"}
                for i in range(10)
            ]},
            media={str(i): b"bytes" for i in range(10)},
        )
        patch_drive(monkeypatch, files)
        urls = img.fetch_brand_drive_images(
            folder_id="F1", access_token="tok", style_code="ABC", vendor="V", max_images=3
        )
        assert len(urls) == 3

    def test_a_failed_download_skips_that_file_only(self, image_dir, monkeypatch):
        class _PartialFiles(FakeFiles):
            def get_media(self, fileId=None, **kwargs):
                self.media_calls.append(fileId)
                if fileId == "bad":
                    return _Executable(None, error=RuntimeError("403"))
                return _Executable(b"bytes")

        files = _PartialFiles(listing={"files": [
            {"id": "bad", "name": "ABC_1.jpg", "mimeType": "image/jpeg"},
            {"id": "good", "name": "ABC_2.jpg", "mimeType": "image/jpeg"},
        ]})
        patch_drive(monkeypatch, files)

        urls = img.fetch_brand_drive_images(
            folder_id="F1", access_token="tok", style_code="ABC", vendor="V"
        )
        assert len(urls) == 1
        assert urls[0].endswith("ABC_2.jpg")

    def test_filenames_are_sanitised_before_hitting_the_filesystem(self, image_dir, monkeypatch):
        files = FakeFiles(
            listing={"files": [
                {"id": "a", "name": "../../escape ABC.jpg", "mimeType": "image/jpeg"}
            ]},
            media={"a": b"bytes"},
        )
        patch_drive(monkeypatch, files)

        urls = img.fetch_brand_drive_images(
            folder_id="F1", access_token="tok", style_code="ABC", vendor="V"
        )
        # The property that matters is that nothing escapes the target
        # directory: separators are replaced, so no path segment can traverse.
        assert "/../" not in urls[0]
        assert not urls[0].split("/api/v1/images/")[1].startswith(".")
        assert not (image_dir.parent / "escape ABC.jpg").exists()

        written = list((image_dir / "drive").rglob("*.jpg"))
        assert len(written) == 1
        assert image_dir in written[0].parents

    def test_missing_folder_or_token_makes_no_call(self, image_dir, monkeypatch):
        files = FakeFiles()
        patch_drive(monkeypatch, files)
        assert img.fetch_brand_drive_images("", "tok", "ABC", "V") == []
        assert img.fetch_brand_drive_images("F1", "", "ABC", "V") == []
        assert files.list_calls == []
