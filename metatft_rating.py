import requests

RATING_CHANGES_URL = "https://api.metatft.com/public/profile/rating_changes/{region}/{game_name}/{tag_line}"

# Verified against real data: rating_numeric = tier_index*400 + (4-division)*100 + LP,
# e.g. "GOLD III 43 LP" -> 3*400 + 100 + 43 = 1343. Tiers below are 400 apart; Master+
# has no divisions (LP just keeps climbing past the Diamond cap).
RANK_TIERS = ["Iron", "Bronze", "Silver", "Gold", "Platinum", "Emerald", "Diamond"]
RANK_TIER_SHORT = ["I", "B", "S", "G", "P", "E", "D"]
DIVISION_LABELS = {0: "4", 1: "3", 2: "2", 3: "1"}


def short_rank_label(rating_numeric: int) -> str:
    """'GOLD III 43 LP' (1343) -> 'G3'. Master+ (no divisions) -> 'M+something'."""
    tier_index, remainder = divmod(rating_numeric, 400)
    if tier_index < len(RANK_TIER_SHORT):
        division_index, _lp = divmod(remainder, 100)
        return RANK_TIER_SHORT[tier_index] + DIVISION_LABELS.get(division_index, "")
    return "M+" + str(rating_numeric - len(RANK_TIER_SHORT) * 400)


def fetch_rating_history(game_name: str, tag_line: str, region: str = "euw1", queue_id: int = 1100) -> list:
    """
    Pulls this player's LP-change-per-game history from metatft.com's public
    profile API — found via their own site's JS bundle, no API key needed.
    Our own database only has rank snapshots going forward from whenever
    watcher.py first ran, so this is the only way to show a full rank
    progression graph for games already played. Returns oldest-first.
    """
    url = RATING_CHANGES_URL.format(region=region, game_name=game_name, tag_line=tag_line)
    response = requests.get(url, params={"queue": queue_id}, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
    response.raise_for_status()
    changes = response.json().get("rating_changes", [])
    return list(reversed(changes))  # API returns newest-first
