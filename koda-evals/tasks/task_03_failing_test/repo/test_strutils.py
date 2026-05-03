from strutils import reverse_words, is_palindrome, count_vowels


def test_reverse_words_basic():
    assert reverse_words("hello world") == "world hello"


def test_reverse_words_three():
    assert reverse_words("a b c") == "c b a"


def test_reverse_words_empty():
    assert reverse_words("") == ""


def test_palindrome_simple():
    assert is_palindrome("Racecar") is True


def test_palindrome_phrase():
    assert is_palindrome("A man a plan a canal Panama") is True


def test_not_palindrome():
    assert is_palindrome("hello") is False


def test_count_vowels():
    assert count_vowels("hello") == 2
    assert count_vowels("AEIOU") == 5
    assert count_vowels("xyz") == 0
