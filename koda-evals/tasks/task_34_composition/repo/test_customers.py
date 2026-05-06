from customers import Customer, PremiumCustomer

def test_customer():
    c = Customer("Alice", 0.05)
    assert c.discount() == 0.05

def test_premium():
    p = PremiumCustomer("Bob", 0.05, 0.1)
    assert p.discount() == 0.15
    assert p.name == "Bob"

def test_premium_zero_base():
    p = PremiumCustomer("Carol", 0.0, 0.2)
    assert p.discount() == 0.2
