def calculate_tax(price, rate, discount):
    subtotal = price * (1 - discount)
    tax = subtotal * rate
    total = subtotal + tax
    return total


def double(x):
    return x * 2


class InvoiceProcessor:
    def __init__(self, client_name, items):
        self.client_name = client_name
        self.items = items
        self.processed = False

    def process(self):
        validated = [item for item in self.items if item > 0]
        self.items = validated
        self.processed = True
        return len(validated)
