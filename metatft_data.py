import json
import os
import re
import requests

CACHE_FILE = "metatft_data_cache.json"
DATA_URL = "https://data.metatft.com/lookups/TFTSet18_latest_en_us.json"

_in_memory_cache = None


def fetch_metatft_data(force_refresh: bool = False) -> dict:
    """
    Downloads metatft's structured Set 18 data dump (units, items, traits,
    etc. with resolved per-star values and explicit icon attribution in
    description markup) — replaces the tactics.tools HTML scrape.
    """
    global _in_memory_cache
    if _in_memory_cache is not None and not force_refresh:
        return _in_memory_cache

    if os.path.exists(CACHE_FILE) and not force_refresh:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _in_memory_cache = json.load(f)
            return _in_memory_cache

    print("Downloading champion/item data from metatft.com...")
    response = requests.get(DATA_URL, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    data = response.json()

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)

    _in_memory_cache = data
    return data


def normalize_champion_name(name: str) -> str:
    """
    Normalizes cosmetic naming differences between sources —
    e.g. our data's 'Lux (Coven)' vs a plain 'Lux Coven'.
    """
    return re.sub(r'[()]', '', name).strip()


def build_metatft_champion_lookup(champion_data: dict) -> dict:
    """
    metatft's champion apiNames (e.g. "TFT18_Ezreal") don't match this
    project's apiName space (e.g. "DA_18_Ezreal", from CommunityDragon/
    Riot's match API) — confirmed by direct comparison, not assumed. So
    champions are joined by display name, same as the old tactics.tools
    pipeline, just against a clean structured record instead of scraped
    text. Items don't need this — see build_metatft_item_lookup.
    """
    data = fetch_metatft_data()
    by_name = {}
    for unit in data["units"]:
        if unit.get("name"):
            by_name[normalize_champion_name(unit["name"])] = unit

    lookup = {}
    misses = []
    for character_id, champ in champion_data.items():
        name = normalize_champion_name(champ.get("name", ""))
        entry = by_name.get(name)
        if entry:
            lookup[character_id] = entry
        else:
            misses.append(champ.get("name", character_id))

    if misses:
        print(f"metatft champion lookup: no match for {len(misses)} champion(s): {misses}")

    return lookup


def build_metatft_item_lookup() -> dict:
    """
    Most item apiNames match this project's item ID strings exactly, but
    not all — some DB rows were recorded under an older item apiName
    (e.g. "TFT_Item_SpearOfShojin") than metatft's current one for the
    same item ("DA_SpearOfShojin"), confirmed by checking real DB rows
    against metatft's data directly. Callers should fall back to
    build_metatft_item_name_lookup() by display name when an apiName
    lookup misses.
    """
    data = fetch_metatft_data()
    return {item["apiName"]: item for item in data["items"]}


def build_metatft_item_name_lookup() -> dict:
    """Fallback for items whose stored apiName doesn't match metatft's current one."""
    data = fetch_metatft_data()
    return {item["name"].lower(): item for item in data["items"] if item.get("name")}
