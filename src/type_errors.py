"""Module with deliberate type errors for testing."""


def greet(name: str) -> str:
    """Should return str but returns int."""
    return 42


def process(items: list[int]) -> int:
    """Should return int but concatenates list with str."""
    return items + "hello"


def working_function(x: int, y: int) -> int:
    """Correctly typed function."""
    return x + y
