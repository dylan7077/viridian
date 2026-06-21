"""Lock the EUR price source: current market (trend) over the stale all-time average.
Pure function, no network. Run: python3 scripts/test_pricing.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import pricing  # noqa: E402


def eur(cm):
    return pricing._eur_market({"cardmarket": cm})


def main():
    # trend wins when present (the current market value)
    assert eur({"avg": 512.96, "trend": 687.37, "avg7": 463.52, "avg30": 510.67}) == 687.37
    # falls back through recent averages before the all-time avg
    assert eur({"avg": 5.0, "avg30": 6.0}) == 6.0
    assert eur({"avg": 5.0, "avg7": 7.0}) == 7.0
    # all-time avg only as last resort
    assert eur({"avg": 5.0}) == 5.0
    # nothing -> None (caller handles)
    assert eur({}) is None
    print("EUR pricing prefers current (trend) over stale all-time avg — locked.")


if __name__ == "__main__":
    main()
