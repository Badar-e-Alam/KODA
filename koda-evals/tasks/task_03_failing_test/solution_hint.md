# Solution

Two bugs in `strutils.py`:

1. `reverse_words` joins with `","` instead of `" "`:
   ```python
   return " ".join(reversed(s.split()))
   ```
2. `is_palindrome` compares the cleaned string to itself:
   ```python
   return cleaned == cleaned[::-1]
   ```
