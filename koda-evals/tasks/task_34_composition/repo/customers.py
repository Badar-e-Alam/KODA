class Customer:
    def __init__(self, name, base_discount=0.0):
        self.name = name
        self.base_discount = base_discount

    def discount(self):
        return self.base_discount

class PremiumCustomer(Customer):
    def __init__(self, name, base_discount=0.0, bonus=0.1):
        super().__init__(name, base_discount)
        self.bonus = bonus

    def discount(self):
        return super().discount() + self.bonus
