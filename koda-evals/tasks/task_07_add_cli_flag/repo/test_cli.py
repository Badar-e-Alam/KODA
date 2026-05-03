import subprocess
import sys
import tempfile
import os


SAMPLE = "the quick brown fox\njumps over the lazy dog\nhello world\n"


def _make_file():
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(SAMPLE)
    return path


def _run(args):
    return subprocess.run(
        [sys.executable, "wc_lite.py", *args],
        capture_output=True, text=True, check=False,
    )


def test_word_count_default():
    path = _make_file()
    try:
        r = _run([path])
        assert r.returncode == 0
        assert f"word count of {path}: 11" in r.stdout
    finally:
        os.unlink(path)


def test_lines_flag_long():
    path = _make_file()
    try:
        r = _run(["--lines", path])
        assert r.returncode == 0
        assert f"line count of {path}: 3" in r.stdout
    finally:
        os.unlink(path)


def test_lines_flag_short():
    path = _make_file()
    try:
        r = _run(["-l", path])
        assert r.returncode == 0
        assert f"line count of {path}: 3" in r.stdout
    finally:
        os.unlink(path)
