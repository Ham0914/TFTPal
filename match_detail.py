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
from tactics_tools_items import fetch_plain_text as fetch_items_text, parse_items

def format_short_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%d %b")

DESCRIPTOR_SENTENCE_PATTERN = re.compile(r'([A-Z][A-Za-z\s]{2,25}:\s[^.]*\.)')
STRUCTURAL_KEYWORDS = {"Passive", "Active", "Adaptor"}


def format_descriptor_sentences(text: str, riftbeast_icon_url: str = None) -> str:
    matches = list(DESCRIPTOR_SENTENCE_PATTERN.finditer(text))
    if not matches:
        return text

    result = text
    for m in reversed(matches):
        sentence = m.group(1)
        keyword = sentence.split(":", 1)[0].strip()

        if keyword in STRUCTURAL_KEYWORDS:
            continue

        # Only treat this as a glossary definition if the keyword was
        # already mentioned earlier in the text as a bare word — that's
        # the "used it, now defining it" pattern. A trigger phrase like
        # "Combat start:" never appears standalone beforehand.
        text_before = text[:m.start()]
        if not re.search(rf'\b{re.escape(keyword)}\b(?!:)', text_before):
            continue

        badge_html = ""
        if riftbeast_icon_url and keyword.endswith("Buff"):
            badge_html = (
                "<span style='display:inline-flex;align-items:center;justify-content:center;"
                "width:16px;height:16px;border-radius:50%;background:transparent;border:none;"
                "margin-right:4px;vertical-align:-1px;position:relative;overflow:hidden;'>"
                "<img src='" + riftbeast_icon_url + "' style='width:12px;height:12px;filter:grayscale(100%);' />"
                "<span style='position:absolute;inset:0;background:#8fae30;mix-blend-mode:color;'></span>"
                "</span>"
            )

        wrapped = f"<br><br><span style='color:#888; font-style:italic;'>{badge_html}{sentence}</span>"
        result = result[:m.start()] + wrapped + result[m.end():]

    return result

_tactics_lookup_cache = None
_tactics_items_cache = None


def get_tactics_item_lookup(known_names: set) -> dict:
    global _tactics_items_cache
    if _tactics_items_cache is None:
        text = fetch_items_text()
        _tactics_items_cache = parse_items(text, known_names)
    return _tactics_items_cache

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

def synthesize_emblem_description(item_name: str) -> str:
    """
    Emblems have no patch-variable numbers — they just grant a fixed
    trait. Rather than depend on a scraped source for this, we derive
    it directly from the item's own name.
    """
    if item_name.endswith("Emblem"):
        trait_name = item_name[: -len("Emblem")].strip()
        return f"Grants the {trait_name} trait."
    return ""

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

STAT_ICON_URLS = {
    "AD": "https://ap.tft.tools/img/general/ad.png?w=14",
    "AP": "https://ap.tft.tools/img/general/ap.png?w=14",
    "Health": "https://ap.tft.tools/img/general/hp.png?w=14",
    "Armor": "https://ap.tft.tools/img/general/armor.png?w=14",
    "Magic Resist": "https://ap.tft.tools/img/general/mr.png?w=14",
    "Attack Speed": "https://ap.tft.tools/img/general/as.png?w=14",
    "Ability Power": "https://ap.tft.tools/img/general/ap.png?w=14",
    "Attack Damage": "https://ap.tft.tools/img/general/ad.png?w=14",
    "Mana Regen": "https://ap.tft.tools/img/general/mana_regen.png?w=14",
    "Durability": "https://ap.tft.tools/img/general/dr.png?w=14",
}

SCALE_TOKEN_ICONS = {
    "scaleAD": STAT_ICON_URLS["AD"],
    "scaleAP": STAT_ICON_URLS["AP"],
    "scaleAS": STAT_ICON_URLS["Attack Speed"],
    "scaleArmor": STAT_ICON_URLS["Armor"],
    "scaleMR": STAT_ICON_URLS["Magic Resist"],
    "scaleHealth": STAT_ICON_URLS["Health"],
    "scaleManaRegen": STAT_ICON_URLS["Mana Regen"],
    "scaleDR": STAT_ICON_URLS["Durability"],
}


def render_scale_icon(token: str) -> str:
    url = SCALE_TOKEN_ICONS.get(token)
    if not url:
        return ""
    return f"<img src='{url}' style='width:14px;height:14px;vertical-align:-2px;' />"

def insert_stat_icons(text: str) -> str:
    """
    Replaces stat name mentions with a small inline icon + the name,
    e.g. 'AD' -> '<img .../> AD'. Longer names are matched first so
    'Magic Resist' isn't partially caught by a shorter overlapping term.
    """
    for name in sorted(STAT_ICON_URLS, key=len, reverse=True):
        icon_html = f"<img src='{STAT_ICON_URLS[name]}' style='width:14px;height:14px;vertical-align:-2px;' /> {name}"
        text = re.sub(rf'\b{re.escape(name)}\b', icon_html, text)
    return text

def insert_damage_type_icons(text: str) -> str:
    """
    'physical damage' implies AD scaling, 'magic damage' implies AP —
    standard TFT convention. Inserts the relevant icon right before
    the number that precedes each phrase.
    """
    def make_replacer(stat_name):
        icon_html = f"<img src='{STAT_ICON_URLS[stat_name]}' style='width:14px;height:14px;vertical-align:-2px;' /> "
        return lambda m: icon_html + m.group(0)

    text = re.sub(r'[\d.]+(?:%?/[\d.]+)*%?\s+physical damage', make_replacer("AD"), text)
    text = re.sub(r'[\d.]+(?:%?/[\d.]+)*%?\s+magic damage', make_replacer("AP"), text)
    return text

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

    def substitute_intro_token(m):
        expr = m.group(1)
        if "*" in expr:
            const_name, multiplier = expr.split("*", 1)
            base = innate_constants.get(const_name.strip())
            if base is None:
                return m.group(0)
            return format_constant_value(const_name, base * float(multiplier))
        base = innate_constants.get(expr)
        if base is None:
            return m.group(0)
        return format_constant_value(expr, base)

    intro_raw = re.sub(r'@([^@]+)@', substitute_intro_token, parts[0])
    scale_tokens_intro = {}
    def stash_intro_icon(m):
        placeholder = f"§ICON{len(scale_tokens_intro)}§"
        scale_tokens_intro[placeholder] = render_scale_icon(m.group(1))
        return placeholder
    intro_raw = re.sub(r'%i:([^%]+)%', stash_intro_icon, intro_raw)
    intro = strip_markup_tags(intro_raw)
    for placeholder, icon_html in scale_tokens_intro.items():
        intro = intro.replace(placeholder, icon_html)
    intro = insert_damage_type_icons(intro)
    intro = insert_stat_icons(intro)

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
        scale_tokens = {}
        def stash_icon(m):
            placeholder = f"§ICON{len(scale_tokens)}§"
            scale_tokens[placeholder] = render_scale_icon(m.group(1))
            return placeholder
        row_text = re.sub(r'%i:([^%]+)%', stash_icon, row_text)

        clean_text = strip_markup_tags(row_text)
        for placeholder, icon_html in scale_tokens.items():
            clean_text = clean_text.replace(placeholder, icon_html)
        clean_text = insert_damage_type_icons(clean_text)
        clean_text = insert_stat_icons(clean_text)

        resolved.append({
            "text": clean_text,
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

def insert_composite_scaling_icons(text: str, breakdown_raw: str) -> str:
    """
    Handles shield/heal formulas like '400/500/2000 + 130/234/421.2',
    which combine two different scaling types. Checks the breakdown
    section for a '=' formula (e.g. '500 + 234 = 10% + 500') to
    determine which term is percentage-based (Health) vs flat (AP).
    """
    pattern = re.compile(r'([\d.]+(?:/[\d.]+)*)\s*\+\s*([\d.]+(?:/[\d.]+)*)')

    def replacer(m):
        first, second = m.group(1), m.group(2)
        # Look for this pair's formula in the breakdown text to see
        # which side is the percentage term.
        formula_match = re.search(
            rf'{re.escape(first.split("/")[0])}\s*\+\s*{re.escape(second.split("/")[0])}\s*=\s*([\d.]+)%',
            breakdown_raw
        )
        second_is_percent = formula_match is not None

        ap_icon = f"<img src='{STAT_ICON_URLS['AP']}' style='width:14px;height:14px;vertical-align:-2px;' />"
        hp_icon = f"<img src='{STAT_ICON_URLS['Health']}' style='width:14px;height:14px;vertical-align:-2px;' />"

        if second_is_percent:
            return f"{ap_icon} {first} + {hp_icon} {second}"
        return f"{ap_icon} {first} + {ap_icon} {second}"

    return pattern.sub(replacer, text)

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
    all_item_names = {item["name"] for item in item_data.values()}
    tactics_items = get_tactics_item_lookup(all_item_names)

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
        item_details = []
        for item_id in item_ids:
            item = item_data.get(item_id)
            if not item:
                continue
            icon_url = tex_path_to_url(item["icon"]) if item.get("icon") else None
            item_name = item["name"]

            desc = tactics_items.get(item_name, {}).get("description", "")
            if not desc:
                desc = synthesize_emblem_description(item_name)
            else:
                desc = insert_damage_type_icons(desc)
                desc = insert_stat_icons(desc)
                desc = format_descriptor_sentences(desc)

            composition = item.get("composition") or []
            component_icons = []
            for comp_id in composition:
                comp_item = item_data.get(comp_id)
                if comp_item and comp_item.get("icon"):
                    component_icons.append(tex_path_to_url(comp_item["icon"]))

            item_details.append({
                "name": item_name,
                "icon_url": icon_url,
                "description": desc,
                "component_icons": component_icons,
            })

        ability = champ.get("ability") if champ else None
        ability_name = ability["name"] if ability else ""

        tactics_entry = tactics_abilities.get(character_id)
        if tactics_entry and tactics_entry.get("description"):
            ability_desc = resolve_tactics_text(tactics_entry["description"], star_level)

            riftbeast_icon_url = None
            if champ and "Riftbeast" in (champ.get("traits") or []):
                riftbeast_trait_info = trait_by_name.get("Riftbeast")
                if riftbeast_trait_info and riftbeast_trait_info.get("icon"):
                    riftbeast_icon_url = tex_path_to_url(riftbeast_trait_info["icon"])

            ability_desc = format_descriptor_sentences(ability_desc, riftbeast_icon_url)
            ability_desc = insert_composite_scaling_icons(ability_desc, tactics_entry.get("breakdown_raw", ""))
            ability_desc = insert_damage_type_icons(ability_desc)
            ability_desc = insert_stat_icons(ability_desc)
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
            "item_details": item_details,
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