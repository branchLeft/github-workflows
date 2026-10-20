"""A typed helper, so that the type check and the lint have a Python file to read."""


def greeting(name: str) -> str:
    """Return the greeting for a name."""
    return f"hello {name}"
