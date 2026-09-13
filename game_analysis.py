from game_data import build_champion_lookup, _load_raw_data


def stage_round(last_round: int) -> str:
    """
    Converts a cumulative round number (e.g. 35) into a stage-round
    display (e.g. '6-3'). Stage 1 has 4 rounds; every stage after
    that has 7 rounds.
    """
    if last_round <= 4:
        return f"1-{last_round}"

    offset = last_round - 5
    stage = 2 + offset // 7
    round_num = offset % 7 + 1
    return f"{stage}-{round_num}"


def get_champion_costs(set_number: int = 18) -> dict:
    """
    Returns a dict mapping character_id to its base shop cost.
    """
    data = _load_raw_data()
    champions = data["sets"][str(set_number)]["champions"]
    return {champ["apiName"]: champ["cost"] for champ in champions}


def board_value(units: list, set_number: int = 18) -> int:
    """
    Estimates the gold value of a board based on unit costs and star levels,
    using the standard 3x-per-star-level combine ratio (1x/3x/9x/27x).
    Note: a 4-star unit's true cost may be lower than 27x if it came from
    a trait mechanic rather than combining copies (e.g. Set 18's Solar
    trait) — this is a combine-value estimate, not a literal gold spent figure.
    Does NOT include items, since Riot doesn't provide a gold value for those.
    """
    costs = get_champion_costs(set_number)
    multipliers = {1: 1, 2: 3, 3: 9, 4: 27}
    total = 0
    for character_id, star_level, _items in units:
        base_cost = costs.get(character_id, 0)
        total += base_cost * multipliers.get(star_level, 1)
    return total

def tex_path_to_url(path: str) -> str:
    """
    Converts a raw game asset path (e.g. 'assets/characters/.../icon.tex')
    into an actual loadable image URL from CommunityDragon.
    """
    converted = path.lower().replace(".tex", ".png")
    return f"https://raw.communitydragon.org/latest/game/{converted}"


def build_champion_data(set_number: int = 18) -> dict:
    """
    Like build_champion_lookup, but returns the FULL champion dict
    (name, cost, squareIcon, etc.) instead of just the name — we need
    the icon path and cost here, not just the readable name.
    """
    data = _load_raw_data()
    champions = data["sets"][str(set_number)]["champions"]
    return {champ["apiName"]: champ for champ in champions}


def build_item_data() -> dict:
    """
    Same idea for items — full dict, so we can get the icon path.
    """
    data = _load_raw_data()
    return {item["apiName"]: item for item in data["items"]}