def read_large_file(path):
    """Read entire file into memory."""
    with open(path, "r") as f:
        return f.readlines()
