from datetime import datetime
from database import get_game, get_units_for_match
from game_data import build_champion_data, build_item_data, tex_path_to_url
from game_analysis import stage_round, board_value
from database import get_game, get_units_for_match, get_traits_for_match
from game_data import build_champion_data, build_item_data, build_trait_data, tex_path_to_url
import re
from game_data import build_champion_data, build_item_data, build_trait_data, build_companion_lookup, companion_icon_url, tex_path_to_url
from game_data import (
    build_champion_data, build_item_data, build_trait_data,
    build_companion_lookup, companion_icon_url, tex_path_to_url,
    build_trait_static_data,
)
from tactics_tools_data import fetch_plain_text as fetch_tactics_text, parse_champions, normalize_champion_name

def format_short_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%d %b")

_tactics_lookup_cache = None


def get_tactics_ability_lookup(known_names: dict) -> dict:
    """
    Maps our own character_id -> tactics.tools ability data, matching
    by champion display name (normalized to handle formatting
    differences like 'Lux (Coven)' vs 'Lux Coven').
    """
    global _tactics_lookup_cache
    if _tactics_lookup_cache is None:
        text = fetch_tactics_text()
        parsed = parse_champions(text)
        _tactics_lookup_cache = {normalize_champion_name(k): v for k, v in parsed.items()}

    result = {}
    for character_id, display_name in known_names.items():
        normalized = normalize_champion_name(display_name)
        entry = _tactics_lookup_cache.get(normalized)
        if entry:
            result[character_id] = entry
    return result

def time_ago(timestamp_ms: int) -> str:
    then = datetime.fromtimestamp(timestamp_ms / 1000)
    seconds = (datetime.now() - then).total_seconds()

    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    else:
        return f"{int(seconds // 86400)}d ago"

def strip_markup_tags(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '<br>', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(?!br)[a-zA-Z][^>]*>', '', text)
    text = text.replace('\r', '').replace('\\n', ' ')
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def clean_game_text(text: str) -> str:
    """
    For champion ability text — scaling values can't be resolved,
    so placeholders become '?'.
    """
    if not text:
        return ""
    text = re.sub(r'@[^@]+@', '?', text)
    text = re.sub(r'%i:[^%]+%', '', text)
    return strip_markup_tags(text)

def resolve_tactics_text(description: str, star_level: int) -> str:
    def pick_star_value(m):
        values = m.group(0).split("/")
        idx = min(star_level - 1, len(values) - 1)
        return values[idx]

    return re.sub(r'\d+(?:\.\d+)?(?:%?/\d+(?:\.\d+)?%?){1,}', pick_star_value, description)

def format_constant_value(name: str, value) -> str:
    if isinstance(value, float):
        rounded = round(value, 2)
        if rounded == int(rounded):
            return str(int(rounded))
        return str(rounded)
    return str(value)


def resolve_trait_breakpoints(trait_static: dict, num_units_player: int) -> dict:
    if not trait_static:
        return {"intro": "", "breakpoints": []}

    text = trait_static.get("tooltip_text", "")
    parts = text.split("<row>")
    row_texts = [p.split("</row>")[0] for p in parts[1:]]

    innate_constants = {}
    for group in trait_static.get("innate_trait_sets", []):
        for c in group.get("constants", []):
            innate_constants[c["name"]] = c["value"]

    intro_raw = parts[0]
    intro_raw = re.sub(
        r'@([^@]+)@',
        lambda m: format_constant_value(m.group(1), innate_constants[m.group(1)]) if m.group(1) in innate_constants else m.group(0),
        intro_raw
    )
    intro = strip_markup_tags(intro_raw)

    breakpoints = trait_static.get("conditional_trait_sets", [])
    resolved = []
    for i, bp in enumerate(breakpoints):
        row_text = row_texts[i] if i < len(row_texts) else ""
        row_text = row_text.replace("<br>", " ")
        row_text = row_text.replace("@MinUnits@", str(bp.get("min_units", "")))

        constants_by_name = dict(innate_constants)
        for c in bp.get("constants", []):
            constants_by_name[c["name"]] = c["value"]

        def substitute_token(match):
            expr = match.group(1)
            if "*" in expr:
                const_name, multiplier = expr.split("*", 1)
                base = constants_by_name.get(const_name.strip())
                if base is None:
                    return match.group(0)
                return format_constant_value(const_name, base * float(multiplier))
            base = constants_by_name.get(expr)
            if base is None:
                return match.group(0)
            return format_constant_value(expr, base)

        row_text = re.sub(r'@([^@]+)@', substitute_token, row_text)
        row_text = re.sub(r'%i:[^%]+%', '', row_text)

        resolved.append({
            "text": strip_markup_tags(row_text),
            "reached": num_units_player >= bp.get("min_units", 0),
        })

    return {"intro": intro, "breakpoints": resolved}

def build_trait_name_lookup(set_number: int = 18) -> dict:
    """Maps a trait's display name -> its full data, for champion tooltips."""
    from game_data import build_trait_data
    return {t["name"]: t for t in build_trait_data(set_number).values()}

def placement_color(placement: int) -> str:
    if placement == 1:
        return "#d4af37"  # gold
    elif placement <= 4:
        return "#3ba55c"  # green
    else:
        return "#c0392b"  # red

def ordinal(n: int) -> str:
    """
    Converts a number into its ordinal form: 1 -> '1st', 2 -> '2nd',
    3 -> '3rd', 4 -> '4th', 11 -> '11th', etc.
    """
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

def trait_style_color(style: int) -> str:
    return {0: "#555555", 1: "#cd7f32", 2: "#c0c0c0", 3: "#ffd700", 4: "#e066ff"}.get(style, "#555555")    

def corrected_trait_style(trait_name: str, num_units: int, style: int) -> int:
    """
    Riot's raw 'style' value marks some small traits as prismatic (4) even
    at a modest unit count. This is an unverified heuristic, not confirmed
    against Riot's actual tier definitions — treat traits with 6 or fewer
    units as gold instead when marked prismatic, since experience suggests
    this set hasn't produced a real prismatic below that count.

    DA_Riftbeast18 is a known exception: even at 10 units, it's genuinely
    gold, not prismatic, so it's hardcoded here regardless of unit count.
    """
    if style != 4:
        return style
    if trait_name == "DA_Riftbeast18":
        return 3
    if num_units <= 6:
        return 3
    return style

def get_match_detail(match_id: str, game_name: str, tag_line: str) -> dict:
    game_row = get_game(match_id)
    (_, placement, level, gold_left, last_round, set_number,
     game_datetime, queue_id, rank_tier, rank_division, league_points, total_damage,
     companion_content_id, companion_item_id) = game_row

    raw_units = get_units_for_match(match_id)
    raw_traits = get_traits_for_match(match_id)
    trait_data = build_trait_data(set_number)

    traits_display = []

    def trait_sort_key(t):
        _, _, style, _, tier_total = t
        is_active = style != 0
        is_unique_active = tier_total == 1 and is_active
        return (not is_active, not is_unique_active, -style)  # unique traits first, then by style descending

    trait_static_lookup = build_trait_static_data()

    for trait_name, num_units, style, tier_current, tier_total in sorted(raw_traits, key=trait_sort_key):
        trait_info = trait_data.get(trait_name)
        name = trait_info["name"] if trait_info else trait_name
        icon_url = tex_path_to_url(trait_info["icon"]) if trait_info and trait_info.get("icon") else None
        display_style = corrected_trait_style(trait_name, num_units, style)
        is_unique = tier_total == 1 and style != 0
        if is_unique:
            color = "#ff8c00"  # bright orange for unique traits
        else:
            display_style = corrected_trait_style(trait_name, num_units, style)
            color = trait_style_color(display_style)

        tooltip = resolve_trait_breakpoints(trait_static_lookup.get(trait_name), num_units)
        traits_display.append({
            "name": name,
            "icon_url": icon_url,
            "color": color,
            "num_units": num_units,
            "tooltip": tooltip,
        })
    champion_data = build_champion_data(set_number)
    item_data = build_item_data()

    trait_by_name = build_trait_name_lookup(set_number)
    known_names = {cid: c["name"] for cid, c in champion_data.items()}
    tactics_abilities = get_tactics_ability_lookup(known_names)

    units_display = []
    for character_id, star_level, items_string in raw_units:
        champ = champion_data.get(character_id)
        name = champ["name"] if champ else character_id
        portrait_url = tex_path_to_url(champ["squareIcon"]) if champ and champ.get("squareIcon") else None
        cost = champ["cost"] if champ else 1

        item_ids = items_string.split(", ") if items_string else []
        item_icons = []
        for item_id in item_ids:
            item = item_data.get(item_id)
            item_icons.append(tex_path_to_url(item["icon"]) if item and item.get("icon") else None)

        ability = champ.get("ability") if champ else None
        ability_name = ability["name"] if ability else ""

        tactics_entry = tactics_abilities.get(character_id)
        if tactics_entry and tactics_entry.get("description"):
            ability_desc = resolve_tactics_text(tactics_entry["description"], star_level)
        else:
            ability_desc = clean_game_text(ability["desc"]) if ability else ""

        stats = champ.get("stats", {}) if champ else {}
        initial_mana = int(stats.get("initialMana") or 0)
        max_mana = int(stats.get("mana") or 0)

        splash_path = None
        splash_is_fallback = True
        if champ:
            if champ.get("icon") and champ["icon"] != "None":
                splash_path = champ["icon"]
                splash_is_fallback = False
            else:
                for key in ("squareIcon", "tileIcon"):
                    candidate = champ.get(key)
                    if candidate and candidate != "None":
                        splash_path = candidate
                        break
        splash_url = tex_path_to_url(splash_path) if splash_path else None

        unit_traits = []
        for tname in (champ.get("traits") or []):
            tinfo = trait_by_name.get(tname)
            unit_traits.append({
                "name": tname,
                "icon_url": tex_path_to_url(tinfo["icon"]) if tinfo and tinfo.get("icon") else None,
            })

        units_display.append({
            "name": name,
            "star_level": star_level,
            "portrait_url": portrait_url,
            "item_icons": item_icons,
            "cost": cost,
            "ability_name": ability_name,
            "ability_desc": ability_desc,
            "initial_mana": initial_mana,
            "max_mana": max_mana,
            "splash_url": splash_url,
            "unit_traits": unit_traits,
            "splash_is_fallback": splash_is_fallback,
        })

    value = board_value(raw_units, set_number)

    companion_lookup = build_companion_lookup()
    companion_info = companion_lookup.get(companion_content_id)
    companion_icon = companion_icon_url(companion_info["loadoutsIcon"]) if companion_info else None
    companion_name = companion_info["name"] if companion_info else None

    return {
        "player_name": game_name,
        "tag_line": tag_line,
        "placement": ordinal(placement),
        "placement_color": placement_color(placement),
        "stage": stage_round(last_round),
        "damage": total_damage,
        "board_value": value,
        "rank_display": f"{rank_tier} {rank_division} ({league_points} LP)" if rank_tier else "Not tracked for this game",
        "short_date": format_short_date(game_datetime),
        "time_ago": time_ago(game_datetime),
        "units": units_display,
        "traits": traits_display,
        "companion_icon": companion_icon,
        "companion_name": companion_name,
    }