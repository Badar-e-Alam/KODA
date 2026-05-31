#!/usr/bin/env python3
"""Main entry point for the project."""

from utils import capitalize


def main():
    """Run the main program."""
    sample_text = "hello world"
    result = capitalize(sample_text)
    print(f"Original: {sample_text}")
    print(f"Capitalized: {result}")


if __name__ == "__main__":
    main()
