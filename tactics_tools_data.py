import os
import re
import requests
from bs4 import BeautifulSoup

CACHE_FILE = "tactics_tools_cache.txt"
PAGE_URL = "https://tactics.tools/info/units"

_in_memory_cache = None


def fetch_plain_text(force_refresh: bool = False) -> str:
    """
    Downloads the Set 18 units info page and extracts its visible text,
    in document order — same shape of output as reading the rendered
    page in a browser, just done headlessly via requests + BeautifulSoup.
    """
    global _in_memory_cache
    if _in_memory_cache is not None and not force_refresh:
        return _in_memory_cache

    if os.path.exists(CACHE_FILE) and not force_refresh:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _in_memory_cache = f.read()
            return _in_memory_cache

    print("Downloading champion ability data from tactics.tools...")
    response = requests.get(PAGE_URL, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text("\n", strip=True)

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(text)

    _in_memory_cache = text
    return text

def normalize_champion_name(name: str) -> str:
    """
    Normalizes cosmetic naming differences between sources —
    e.g. our data's 'Lux (Coven)' vs tactics.tools' 'Lux Coven'.
    """
    return re.sub(r'[()]', '', name).strip()

def _consolidate_fragmented_lines(lines: list) -> list:
    """
    The site renders bracketed number lists as one span per character
    (e.g. '[', '60', '/', '90', ']' on five separate lines). This merges
    any consecutive run of such fragments back into a single token.
    """
    fragment_pattern = re.compile(r'^[\[\]\d./%]*$')
    result = []
    i = 0
    while i < len(lines):
        if fragment_pattern.fullmatch(lines[i]) and lines[i] != "":
            run = []
            while i < len(lines) and fragment_pattern.fullmatch(lines[i]) and lines[i] != "":
                run.append(lines[i])
                i += 1
            result.append("".join(run))
        else:
            result.append(lines[i])
            i += 1
    return result


def parse_champions(text: str) -> dict:
    lines = [l for l in text.split("\n") if l.strip()]
    lines = _consolidate_fragmented_lines(lines)

    boundaries = [
        i for i in range(len(lines) - 2)
        if lines[i] == "Ability" and lines[i + 1] == "Traits" and lines[i + 2] == "Stats"
    ]

    champions = {}
    start = 0
    for boundary in boundaries:
        block = lines[start:boundary]

        # Find where the real champion content begins within this slice,
        # discarding any leading junk (nav bar text, filter labels, etc.)
        name_start = next(
            (i for i in range(len(block) - 1)
             if re.match(r'^[A-Z]', block[i]) and re.fullmatch(r'[1-5]', block[i + 1])),
            None
        )

        if name_start is not None:
            parsed = _parse_one_block(block[name_start:])
            if parsed:
                champions[parsed["name"]] = parsed

        start = boundary + 3

    return champions

def _parse_one_block(lines: list) -> dict:
    if len(lines) < 4:
        return None

    name = lines[0]
    if not re.fullmatch(r'[1-5]', lines[1]):
        return None
    cost = int(lines[1])

    mana_idx = next((i for i, l in enumerate(lines) if re.fullmatch(r'\d+/\d+', l)), None)

    if mana_idx is not None and mana_idx >= 3:
        traits = lines[2:mana_idx - 1]
        ability_name = lines[mana_idx - 1]
        start_mana, max_mana = map(int, lines[mana_idx].split("/"))
        desc_start = mana_idx + 1
    else:
        # No mana bar (e.g. Kayle) — traits run until the ability name,
        # which we identify as the last all-traits-then-one-more-line
        # before the description text genuinely begins (first long line).
        desc_start = next((i for i in range(2, len(lines)) if len(lines[i]) > 40), len(lines))
        traits = lines[2:desc_start - 1]
        ability_name = lines[desc_start - 1] if desc_start > 2 else ""
        start_mana, max_mana = None, None

    bracket_idx = next(
        (i for i in range(desc_start, len(lines)) if re.search(r'\[[\d./%]+\]', lines[i])),
        None
    )

    if bracket_idx is not None:
        label_pattern = re.compile(r'^[A-Za-z][\w\s]{0,30}:$')
        formula_pattern = re.compile(r'^[\d.%+=\s]+$')
        desc_end = bracket_idx
        while desc_end > desc_start:
            candidate = lines[desc_end - 1]
            if label_pattern.fullmatch(candidate) or formula_pattern.fullmatch(candidate):
                desc_end -= 1
            else:
                break
    else:
        desc_end = len(lines)

    desc_lines = lines[desc_start:desc_end]

    breakdown_lines = lines[desc_end:bracket_idx] if bracket_idx else []

    return {
        "name": name,
        "cost": cost,
        "traits": traits,
        "ability_name": ability_name,
        "start_mana": start_mana,
        "max_mana": max_mana,
        "description": " ".join(desc_lines).strip(),
        "breakdown_raw": " ".join(breakdown_lines),
    }