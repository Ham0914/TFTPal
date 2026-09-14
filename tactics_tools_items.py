import os
import re
import requests
from bs4 import BeautifulSoup

CACHE_FILE = "tactics_tools_items_cache.txt"
PAGE_URL = "https://tactics.tools/info/items"

_in_memory_cache = None


def fetch_plain_text(force_refresh: bool = False) -> str:
    global _in_memory_cache
    if _in_memory_cache is not None and not force_refresh:
        return _in_memory_cache

    if os.path.exists(CACHE_FILE) and not force_refresh:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _in_memory_cache = f.read()
            return _in_memory_cache

    print("Downloading item data from tactics.tools...")
    response = requests.get(PAGE_URL, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text("\n", strip=True)

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(text)

    _in_memory_cache = text
    return text


def clean_item_description(desc: str) -> str:
    desc = re.sub(r'\s+([.,:])', r'\1', desc)
    return re.sub(r'\{[^}]*\}', '', desc).strip()


def parse_items(text: str, known_names: set) -> dict:
    lines = [l for l in text.split("\n") if l.strip()]

    name_positions = [
        (i, lines[i]) for i in range(len(lines))
        if lines[i] in known_names
    ]

    stat_line_pattern = re.compile(r'^[\d.%\s]+$')

    items = {}
    for idx, (pos, name) in enumerate(name_positions):
        end = name_positions[idx + 1][0] if idx + 1 < len(name_positions) else len(lines)
        block = lines[pos + 1:end]

        # Skip past any stat-number line(s) and an optional '+' marker,
        # to find where the actual description prose begins.
        desc_start = 0
        for i, line in enumerate(block):
            if line == "+" or stat_line_pattern.fullmatch(line):
                desc_start = i + 1
            else:
                break

        desc_lines = block[desc_start:]
        description = clean_item_description(" ".join(desc_lines))
        if description:
            items[name] = {"description": description}

    return items