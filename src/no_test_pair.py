"""Module with no corresponding test file."""


def untested_function(data: list[str]) -> list[str]:
    """Process data without any test coverage."""
    cleaned = [x.strip() for x in data]
    filtered = [x for x in cleaned if x]
    return sorted(filtered)
