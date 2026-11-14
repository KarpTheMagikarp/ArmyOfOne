"""
tools/relines.py swaps the cuts' baked scanlines for XIII's status screen ones.
The row maths is checked on its own, then one real clip goes through ffmpeg.
"""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("relines", ROOT / "tools" / "relines.py")
relines = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relines)


def striped(height, level=150.0):
    # the cuts as they shipped: every fifth row down to 55%, over a soft gradient
    rows = np.linspace(level - 30, level + 30, height)
    rows[2::5] *= 0.55
    return rows


def test_the_old_lines_divide_back_out():
    rows = striped(720)
    clean = rows / relines.line_gain(rows, 720)
    assert np.allclose(clean, np.linspace(120, 180, 720), rtol=0.01)


def test_a_4k_master_s_thicker_lines_still_come_out():
    # the 720p cut is the master scaled down 3x, so a line there is three rows here
    rows = np.linspace(120, 180, 2160)
    for y in range(6, 2160, 15):
        rows[y:y + 3] *= 0.55
    clean = rows / relines.line_gain(rows, 2160)
    assert np.allclose(clean, np.linspace(120, 180, 2160), rtol=0.01)


def test_the_new_lines_keep_the_brightness_and_scale_with_the_clip():
    for height in (720, 1440, 2160):
        lines = relines.status_lines(height, 0.7)
        assert abs(lines.mean() - 1) < 0.01
        # a dark row and a light row every 3 rows a 720p's worth of height
        period = 3 * height // 720
        assert np.allclose(lines[:period * 4], np.tile(lines[:period], 4))
    assert np.all(relines.status_lines(720, 0) == 1)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg to encode with")
def test_a_clip_comes_out_with_the_status_screen_lines(tmp_path):
    w, h = 64, 720
    frame = np.repeat(striped(h)[:, None], w, 1)
    frame = np.repeat(frame[:, :, None], 3, 2).clip(0, 255).astype(np.uint8)
    src = tmp_path / "cut.mp4"
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                            "-r", "60", "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p", str(src)],
                           stdin=subprocess.PIPE)
    enc.stdin.write(frame.tobytes() * 6)
    enc.stdin.close()
    assert enc.wait() == 0

    out = tmp_path / "out.mp4"
    assert relines.reline(src, out, 0.7) == 6
    got = next(relines.frames(out, w, h)).astype(float).mean((1, 2))
    want = np.linspace(120, 180, h) * relines.status_lines(h, 0.7)
    # within a couple of levels once it's been through a lossy 4:2:0 encode
    assert np.abs(got - want)[4:-4].mean() < 2.5
    assert not list(tmp_path.glob("*.passlog*"))


def clean_clip(path, w, h, n=4):
    frame = np.repeat(np.linspace(40, 200, w)[None, :], h, 0)
    frame = np.repeat(frame[:, :, None], 3, 2).astype(np.uint8)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                            "-r", "60", "-i", "-", "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv444p", str(path)],
                           stdin=subprocess.PIPE)
    enc.stdin.write(frame.tobytes() * n)
    enc.stdin.close()
    assert enc.wait() == 0


@pytest.fixture
def project(tmp_path, monkeypatch):
    """a jobs.json with one game and its three cuts, and the tool pointed at it"""
    import json

    (tmp_path / "ui").mkdir()
    (tmp_path / "assets").mkdir()
    (tmp_path / "ui" / "jobs.json").write_text(json.dumps({"games": [{
        "id": "g", "cutMp4": "../assets/cut.mp4", "cut": "../assets/cut.webm",
        "cut720": "../assets/cut_720.webm"}]}))
    monkeypatch.setattr(relines, "UI", tmp_path / "ui")
    monkeypatch.setattr(relines, "ORIGINALS", tmp_path / ".cut-originals")
    monkeypatch.setattr(relines, "CLEAN", tmp_path / ".cut-originals" / "clean")
    # small enough to encode in a test, same proportions between the cuts
    monkeypatch.setattr(relines, "SIZES", {"cutMp4": (48, 2160), "cut": (32, 1440), "cut720": (16, 720)})
    return tmp_path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg to encode with")
def test_a_fresh_master_makes_every_cut_with_only_the_new_lines(project):
    clean_clip(project / "assets" / "cut.mp4", 48, 2160)
    relines.main(0.7, fresh=True)
    for name, (w, h) in (("cut.mp4", (48, 2160)), ("cut.webm", (32, 1440)), ("cut_720.webm", (16, 720))):
        got = next(relines.frames(project / "assets" / name, w, h)).astype(float).mean((1, 2))
        flat = next(relines.frames(project / ".cut-originals" / "cut.mp4", w, h, scale=True)).astype(float).mean((1, 2))
        # nothing was taken out, so every row is the master's times the status screen's
        assert np.abs(got / flat - relines.status_lines(h, 0.7))[8:-8].mean() < 0.02, name
    assert (project / ".cut-originals" / "clean").exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg to encode with")
def test_fresh_refuses_originals_that_still_have_their_lines(project):
    (project / ".cut-originals").mkdir()
    clean_clip(project / ".cut-originals" / "cut.mp4", 48, 2160)
    with pytest.raises(SystemExit, match="delete .cut-originals"):
        relines.main(0.7, fresh=True)
