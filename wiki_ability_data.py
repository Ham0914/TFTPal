import os
import re
import time
import requests

CACHE_FILE = "wiki_ability_cache.txt"
DATA_URL = "https://wiki.leagueoflegends.com/en-us/api.php?action=parse&page=Module:TFTUnitData/data&format=json&prop=wikitext"

_in_memory_cache = None

def format_constant_value(name: str, value) -> str:
    if isinstance(value, float):
        rounded = round(value, 2)
        if rounded == int(rounded):
            return str(int(rounded))
        return str(rounded)
    return str(value)

def fetch_raw_lua(force_refresh: bool = False) -> str:
    """
    Downloads (and caches) the wiki's central Lua data table — one file
    covering every champion, every set, ever. force_refresh=True bypasses
    the disk cache, for use on patch day.
    """
    global _in_memory_cache
    if _in_memory_cache is not None and not force_refresh:
        return _in_memory_cache

    if os.path.exists(CACHE_FILE) and not force_refresh:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _in_memory_cache = f.read()
            return _in_memory_cache

    print("Downloading champion ability data from the wiki...")
    response = requests.get(DATA_URL)
    response.raise_for_status()
    wikitext = response.json()["parse"]["wikitext"]["*"]

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(wikitext)

    _in_memory_cache = wikitext
    return wikitext


def _find_matching_brace(text: str, open_pos: int) -> int:
    """
    Given the position of an opening '{', finds the position of its
    matching '}', correctly ignoring braces that appear inside quoted
    strings (since ability text contains literal '{{...}}' wiki markup
    as part of the string content, not real Lua table structure).
    """
    depth = 0
    in_string = False
    i = open_pos
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    raise ValueError("No matching brace found — malformed or truncated Lua text")


def _extract_field(block: str, field: str, as_number: bool = False):
    """
    Pulls a single ["field"] = value pair out of a Lua block.
    Strings are unquoted; numbers are parsed as float/int.
    """
    if as_number:
        match = re.search(rf'\["{field}"\]\s*=\s*([\d.]+)', block)
        if not match:
            return None
        val = float(match.group(1))
        return int(val) if val == int(val) else val
    match = re.search(rf'\["{field}"\]\s*=\s*"((?:[^"\\]|\\.)*)"', block)
    return match.group(1) if match else None


def _split_champion_blocks(full_text: str) -> dict:
    """
    Splits the whole file into {champion_name: block_text} for every
    top-level champion entry, using brace-aware matching throughout.
    """
    blocks = {}
    for m in re.finditer(r'\["([^"]+)"\]\s*=\s*\{', full_text):
        name = m.group(1)
        open_pos = m.end() - 1
        try:
            close_pos = _find_matching_brace(full_text, open_pos)
        except ValueError:
            continue
        blocks[name] = full_text[open_pos:close_pos + 1]
    return blocks


def _split_set_entries(champion_block: str) -> list:
    """
    Within one champion's block, splits out each numbered set entry
    (e.g. [18] = {...}), again brace-aware.
    """
    entries = []
    for m in re.finditer(r'\[([\d.]+)\]\s*=\s*\{', champion_block):
        set_key = m.group(1)
        open_pos = m.end() - 1
        try:
            close_pos = _find_matching_brace(champion_block, open_pos)
        except ValueError:
            continue
        entries.append((set_key, champion_block[open_pos:close_pos + 1]))
    return entries


def resolve_ability_text(raw_text: str, star_level: int) -> str:
    """
    Converts the wiki's markup into plain text for a specific star level.
    Templates can nest several levels deep, so this resolves from the
    innermost outward, looping until no more markup remains.
    """
    if not raw_text:
        return ""

    def replace_ap(m):
        content = m.group(1).strip()

        # Format: "X to Y N" — linear interpolation across N star levels
        range_match = re.fullmatch(r'([\d.]+)\s+to\s+([\d.]+)\s+(\d+)', content)
        if range_match:
            start, end, steps = float(range_match.group(1)), float(range_match.group(2)), int(range_match.group(3))
            if steps <= 1:
                value = start
            else:
                fraction = min(star_level - 1, steps - 1) / (steps - 1)
                value = start + (end - start) * fraction
            return format_constant_value("", value)

        # Format: "A|B|C" — one explicit value per star
        values = content.split("|")
        idx = min(star_level - 1, len(values) - 1)
        return values[idx].strip()

    text = raw_text
    for _ in range(10):  # safety cap — real nesting never goes this deep
        new_text = re.sub(r'\{\{ap\|([^{}]+)\}\}', replace_ap, text)
        new_text = re.sub(r'\{\{tiptft\|([^{}|]*)\|[^{}]*\}\}', r'\1', new_text)
        new_text = re.sub(r'\{\{(?:as|fd)\|([^{}]*)\}\}', r'\1', new_text)
        if new_text == text:
            break
        text = new_text

    # anything left unrecognized: keep its last argument rather than raw markup
    text = re.sub(r'\{\{[^|{}]*\|([^{}]*)\}\}', r'\1', text)
    text = re.sub(r'\{\{[^{}]*\}\}', '', text)

    text = text.replace("<br><br>", "\n\n").replace("<br>", "\n")
    return text.strip()


def build_ability_lookup(known_ability_names: dict) -> dict:
    """
    Builds character_id -> resolved ability data, for a given set.
    known_ability_names: {character_id: ability_display_name} from our
    own champion data — used to find the correct entry among a champion's
    full history, since array index doesn't map cleanly to set number.
    """
    raw = fetch_raw_lua()
    champion_blocks = _split_champion_blocks(raw)
    result = {}

    for character_id, ability_name in known_ability_names.items():
        # our character_id is like "DA_18_Ezreal" — the wiki keys by display name
        display_name = character_id.split("_")[-1] if "_" not in character_id[:6] else None
        # fall back: try every wiki champion block's ability name for a match
        matched_block = None
        for champ_name, champ_block in champion_blocks.items():
            for set_key, entry in _split_set_entries(champ_block):
                if _extract_field(entry, "abilityname") == ability_name:
                    matched_block = entry
                    break
            if matched_block:
                break

        if not matched_block:
            continue

        result[character_id] = {
            "ability_name": ability_name,
            "raw_active": _extract_field(matched_block, "active"),
            "hp": _extract_field(matched_block, "hp", as_number=True),
            "mana": _extract_field(matched_block, "mana", as_number=True),
            "startmana": _extract_field(matched_block, "startmana", as_number=True),
            "ad": _extract_field(matched_block, "ad", as_number=True),
            "as_": _extract_field(matched_block, "as", as_number=True),
            "arm": _extract_field(matched_block, "arm", as_number=True),
            "mr": _extract_field(matched_block, "mr", as_number=True),
        }

    return result

_full_lookup_cache = None


def get_full_ability_lookup(set_number: int = 18) -> dict:
    """
    Builds (once per run) the ability lookup for every current-set champion,
    using our own champion data to know which ability names to look for.
    Cached in memory — Streamlit keeps this process alive between
    interactions, so this only runs the expensive wiki scan once per session.
    """
    global _full_lookup_cache
    if _full_lookup_cache is not None:
        return _full_lookup_cache

    from game_data import build_champion_data
    champions = build_champion_data(set_number)
    known_names = {
        char_id: champ["ability"]["name"]
        for char_id, champ in champions.items()
        if champ.get("ability", {}).get("name")
    }

    _full_lookup_cache = build_ability_lookup(known_names)
    return _full_lookup_cache