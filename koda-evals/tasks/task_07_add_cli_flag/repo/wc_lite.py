"""Tiny CLI: count words in a file."""
import argparse
import sys


def count_words(path: str) -> int:
    with open(path) as f:
        return len(f.read().split())


def count_lines(path: str) -> int:
    with open(path) as f:
        return sum(1 for _ in f)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Count words in a file.")
    parser.add_argument("path", help="path to text file")
    args = parser.parse_args(argv)

    n = count_words(args.path)
    print(f"word count of {args.path}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
