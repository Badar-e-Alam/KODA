"""Temperature conversions and classification."""


def c_to_f(c: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return c * 9 / 5 + 32


def f_to_c(f: float) -> float:
    """Convert Fahrenheit to Celsius."""
    return (f - 32) * 5 / 9


def classify(c: float) -> str:
    """Classify a Celsius temperature.

    < -273.15 -> raises ValueError (below absolute zero)
    <= 0      -> 'freezing'
    < 15      -> 'cold'
    < 25      -> 'mild'
    < 35      -> 'warm'
    >= 35     -> 'hot'
    """
    if c < -273.15:
        raise ValueError(f"below absolute zero: {c}")
    if c <= 0:
        return "freezing"
    if c < 15:
        return "cold"
    if c < 25:
        return "mild"
    if c < 35:
        return "warm"
    return "hot"
