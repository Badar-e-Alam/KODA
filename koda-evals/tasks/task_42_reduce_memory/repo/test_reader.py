import tempfile
from reader import read_large_file

def test_generator():
    # Verify it returns a generator-like iterable
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line1\nline2\nline3\n")
        path = f.name

    result = read_large_file(path)
    # Should be iterable, not a list
    assert not isinstance(result, list)
    lines = list(result)
    assert lines == ["line1\n", "line2\n", "line3\n"]
