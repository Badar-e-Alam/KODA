import pytest
from server import serve_file

def test_normal():
    # This test assumes base_dir exists with a file
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "test.txt").write_text("hello")
        assert serve_file(td, "test.txt") == "hello"

def test_traversal_blocked():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "secret.txt").write_text("secret")
        outside = Path(td).parent
        with pytest.raises(ValueError):
            serve_file(str(outside / "safe"), "../secret.txt")
