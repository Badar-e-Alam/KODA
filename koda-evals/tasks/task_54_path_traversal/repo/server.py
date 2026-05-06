from pathlib import Path

def serve_file(base_dir, filename):
    """Read file from base_dir. VULNERABLE to path traversal."""
    path = Path(base_dir) / filename
    return path.read_text()
