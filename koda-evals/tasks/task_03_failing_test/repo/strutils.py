"""String utilities."""


def reverse_words(s: str) -> str:
    """Reverse the order of words in a string.

    'hello world' -> 'world hello'
    'a b c'       -> 'c b a'
    ''            -> ''
    """
    # BUG 1: split() with no args is fine, but join uses wrong separator
    return ",".join(reversed(s.split()))


def is_palindrome(s: str) -> bool:
    """Return True if s is a palindrome (case-insensitive, ignoring non-alpha).

    'Racecar' -> True
    'A man a plan a canal Panama' -> True
    'hello' -> False
    """
    cleaned = "".join(c.lower() for c in s if c.isalpha())
    # BUG 2: comparing to itself, not the reverse
    return cleaned == cleaned


def count_vowels(s: str) -> int:
    """Count vowels in s (a, e, i, o, u — case-insensitive)."""
    return sum(1 for c in s.lower() if c in "aeiou")
