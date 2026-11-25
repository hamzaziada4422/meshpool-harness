import os


def max_turns(default: int) -> int:
    """An office engine's inner-loop limit under MH_ENGINE_MAX_TURNS (a positive integer caps it; unset, empty or
    invalid leaves the engine's own default). Sheets: its turn budget (default 50, MH_SHEETS_MAX_TURNS). Slides
    (forge): its content repair, storyboard and deck review rounds (default 4, 4 and 3). Data: its turns (default
    30)."""
    try:
        cap = int(os.environ.get("MH_ENGINE_MAX_TURNS", "") or 0)
    except ValueError:
        cap = 0
    return min(default, cap) if cap > 0 else default
