import io
import threading
import zipfile
from pathlib import Path

import pytest

from patcher.download import (
    Cancelled,
    NotEnoughSpace,
    Progress,
    Throttle,
    ensure_space,
    extracted_size,
    sha256_of_file,
    stream_download,
    stream_extract,
)


class FakeResponse:
    """a urlopen stand-in that honours Range the way a real CDN does"""

    def __init__(self, body: bytes, headers: dict, status=200, ignore_range=False):
        self.status = status
        self._offset = 0
        start = 0
        if not ignore_range and "Range" in headers:
            start = int(headers["Range"].split("=")[1].split("-")[0])
            self.status = 206
        self._body = body[start:]
        self.headers = {"Content-Length": str(len(self._body))}
        self.closed = False

    def read(self, size=-1):
        if size is None or size < 0:
            block, self._offset = self._body[self._offset:], len(self._body)
            return block
        block = self._body[self._offset:self._offset + size]
        self._offset += len(block)
        return block

    def close(self):
        self.closed = True


def opener_for(body: bytes, ignore_range=False, fail_after=None):
    seen = []

    def opener(url, headers):
        seen.append(headers)
        response = FakeResponse(body, headers, ignore_range=ignore_range)
        if fail_after is not None:
            # truncates the stream, the way a dropped connection does
            response._body = response._body[:fail_after]
            response.headers["Content-Length"] = str(len(response._body))
        return response

    opener.seen = seen
    return opener


def test_a_large_body_never_lands_in_one_piece(tmp_path):
    body = bytes(range(256)) * 40_000          # ~10MB
    dest = tmp_path / "pack.zip"
    sizes = []

    stream_download("https://x.test/pack.zip", dest, opener=opener_for(body),
                    on_progress=lambda p: sizes.append(p.done))

    assert dest.read_bytes() == body
    # progress arrived while it was still going, not just at the end
    assert len(sizes) >= 2
    assert sizes[-1] == len(body)


def test_an_interrupted_download_resumes_instead_of_restarting(tmp_path):
    body = b"A" * 5000 + b"B" * 5000
    dest = tmp_path / "pack.zip"
    part = dest.with_suffix(".zip.part")
    part.write_bytes(body[:5000])              # a previous attempt got halfway

    opener = opener_for(body)
    stream_download("https://x.test/pack.zip", dest, opener=opener)

    assert opener.seen[0]["Range"] == "bytes=5000-"
    assert dest.read_bytes() == body
    assert not part.exists()


def test_a_server_that_ignores_range_starts_clean(tmp_path):
    """otherwise the resumed bytes get appended to a full body and corrupt it"""
    body = b"A" * 4000 + b"B" * 4000
    dest = tmp_path / "pack.zip"
    dest.with_suffix(".zip.part").write_bytes(b"A" * 4000)

    stream_download("https://x.test/pack.zip", dest, opener=opener_for(body, ignore_range=True))

    assert dest.read_bytes() == body, "range was refused, so the partial file had to be dropped"


def test_nothing_appears_at_the_destination_until_it_is_whole(tmp_path):
    """a killed download must not leave a file that looks finished"""
    body = b"x" * 8000
    dest = tmp_path / "pack.zip"
    cancel = threading.Event()

    def opener(url, headers):
        cancel.set()
        return FakeResponse(body, headers)

    with pytest.raises(Cancelled):
        stream_download("https://x.test/pack.zip", dest, opener=opener, cancel=cancel)

    assert not dest.exists()
    assert dest.with_suffix(".zip.part").exists(), "the partial stays, so a retry can resume"


def test_cancel_is_checked_between_chunks_not_between_files(tmp_path):
    body = b"y" * (4 * 1024 * 1024)
    dest = tmp_path / "big.bin"
    cancel = threading.Event()
    seen = []

    def on_progress(p):
        seen.append(p.done)
        cancel.set()                            # stop after the first chunk

    with pytest.raises(Cancelled):
        stream_download("https://x.test/big.bin", dest, opener=opener_for(body),
                        on_progress=on_progress, cancel=cancel)

    assert seen and seen[0] < len(body), "it stopped mid-file, not at a file boundary"


def test_a_download_too_big_for_the_disk_is_refused_before_writing(tmp_path, monkeypatch):
    import patcher.download as dl
    monkeypatch.setattr(dl, "free_space", lambda path: 100 * 1024 * 1024)

    with pytest.raises(NotEnoughSpace) as caught:
        stream_download("https://x.test/huge.zip", tmp_path / "huge.zip",
                        opener=opener_for(b"x"), expected_size=40 * 1024 ** 3)

    assert "GB free" in str(caught.value)
    assert not (tmp_path / "huge.zip.part").exists()


def make_zip(path: Path, files: dict):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def test_extraction_streams_and_reports_bytes_not_files(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {
        "pack/one.imgb": b"1" * 2_000_000,
        "pack/two.imgb": b"2" * 3_000_000,
    })
    out = tmp_path / "out"
    seen = []

    written = stream_extract(archive, out, on_progress=lambda p: seen.append(p))

    assert sorted(written) == ["pack/one.imgb", "pack/two.imgb"]
    assert (out / "pack" / "two.imgb").stat().st_size == 3_000_000
    # progress moved inside a single large member, not once per file
    assert len(seen) >= 2
    assert seen[-1].done == 5_000_000
    assert seen[-1].fraction == 1.0


def test_extraction_checks_space_using_the_archive_index(tmp_path, monkeypatch):
    archive = make_zip(tmp_path / "a.zip", {"big.imgb": b"z" * 1_000_000})
    assert extracted_size(archive) == 1_000_000

    import patcher.download as dl
    monkeypatch.setattr(dl, "free_space", lambda path: 1000)

    with pytest.raises(NotEnoughSpace):
        stream_extract(archive, tmp_path / "out")
    assert not (tmp_path / "out" / "big.imgb").exists()


def test_a_member_escaping_the_target_directory_is_dropped(tmp_path):
    """zip slip: extractall has historically honoured exactly this"""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../escaped.dll", b"bad")
        zf.writestr("..\\..\\escaped_win.dll", b"bad")
        zf.writestr("good/fine.imgb", b"ok")

    out = tmp_path / "out"
    written = stream_extract(archive, out)

    assert written == ["good/fine.imgb"]
    assert not (tmp_path / "escaped.dll").exists()
    assert not (tmp_path.parent / "escaped.dll").exists()


def test_flatten_keeps_only_leaf_names(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {"FF13Fix_164/d3d9.dll": b"dll"})
    written = stream_extract(archive, tmp_path / "out", flatten=True)
    assert written == ["d3d9.dll"]
    assert (tmp_path / "out" / "d3d9.dll").read_bytes() == b"dll"


def test_source_subdir_scopes_extraction(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {
        "dxvk-2.3/x32/d3d9.dll": b"32",
        "dxvk-2.3/x64/d3d9.dll": b"64",
    })
    written = stream_extract(archive, tmp_path / "out", flatten=True, source_subdir="x32")
    assert written == ["d3d9.dll"]
    assert (tmp_path / "out" / "d3d9.dll").read_bytes() == b"32"


def test_checksum_is_computed_without_loading_the_file(tmp_path):
    import hashlib
    big = tmp_path / "big.bin"
    big.write_bytes(b"q" * 5_000_000)
    assert sha256_of_file(big) == hashlib.sha256(b"q" * 5_000_000).hexdigest()


def test_throttle_collapses_a_flood_but_always_emits_the_last_one():
    """6GB at 1MB chunks is 6000 callbacks, which no UI thread wants"""
    seen = []
    now = [0.0]
    throttle = Throttle(seen.append, interval=0.2, clock=lambda: now[0])

    for i in range(100):
        now[0] += 0.01                          # 10ms apart
        throttle(Progress("download", i, 100))
    throttle(Progress("download", 100, 100), final=True)

    assert len(seen) < 20, "should have collapsed a hundred calls"
    assert seen[-1].done == 100, "the final call must always get through"


def test_throttle_with_no_sink_is_harmless():
    Throttle(None)(Progress("download", 1, 2))


def test_install_from_url_streams_and_reports_both_stages(tmp_path):
    """the engine's install path must go through the streaming layer, not bytes"""
    import io
    import zipfile as zf_mod
    from patcher.mods.file_install import install_from_url

    buf = io.BytesIO()
    with zf_mod.ZipFile(buf, "w", zf_mod.ZIP_STORED) as zf:
        zf.writestr("pack/one.imgb", b"a" * 2_000_000)
        zf.writestr("pack/two.imgb", b"b" * 2_000_000)
    payload = buf.getvalue()

    seen = []
    installed = install_from_url("https://x.test/pack.zip", tmp_path / "bin",
                                 fetch=lambda url: payload,
                                 on_progress=seen.append)

    assert sorted(installed) == ["one.imgb", "two.imgb"]
    assert {p.stage for p in seen} == {"download", "extract"}
    assert seen[-1].fraction == 1.0
    # staging must not survive a successful install
    assert not (tmp_path / ".patcher-tmp").exists()


def test_a_failed_install_leaves_no_target_directory(tmp_path):
    from patcher.mods.file_install import install_from_url

    with pytest.raises(ValueError, match="checksum"):
        install_from_url("https://x.test/pack.zip", tmp_path / "bin",
                         fetch=lambda url: b"PK\x03\x04not-a-real-zip",
                         expected_sha256="0" * 64)

    assert not (tmp_path / "bin").exists(), "an empty mod folder is its own kind of lie"
    assert not (tmp_path / ".patcher-tmp" / "pack.zip").exists()


def test_a_cancelled_install_stops_and_cleans_up(tmp_path):
    import threading
    from patcher.download import Cancelled
    from patcher.mods.file_install import install_from_url

    cancel = threading.Event()
    cancel.set()

    with pytest.raises(Cancelled):
        install_from_url("https://x.test/pack.zip", tmp_path / "bin",
                         fetch=lambda url: b"x" * 4_000_000, cancel=cancel)

    assert not (tmp_path / "bin").exists()
