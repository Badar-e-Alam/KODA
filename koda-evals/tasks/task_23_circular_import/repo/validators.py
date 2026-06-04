# This causes circular import when models is loaded
from models import User

def is_email(value):
    return "@" in value

def validate_user(user):
    return isinstance(user, User) and is_email(user.email)
