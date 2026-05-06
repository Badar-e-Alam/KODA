# Solution
Move the `from models import User` in validators.py to inside the `validate_user` function (lazy import), or remove it entirely and use duck typing (`hasattr(user, 'email')`).
