from models import User
from validators import is_email, validate_user

def test_user_valid():
    u = User("alice@example.com")
    assert u.is_valid() is True

def test_user_invalid():
    u = User("not-an-email")
    assert u.is_valid() is False

def test_validate_user():
    u = User("alice@example.com")
    assert validate_user(u) is True
