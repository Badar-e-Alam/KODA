import validators  # circular import

class User:
    def __init__(self, email):
        self.email = email

    def is_valid(self):
        return validators.is_email(self.email)
