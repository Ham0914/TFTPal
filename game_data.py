import requests
import json
import os

CACHE_FILE = "cdragon_data_cache.json"
CDRAGON_URL = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"


_in_memory_cache = None  # holds the parsed data once loaded, for the life of this program run


def _load_raw_data() -> dict:
    """
    Loads the full CommunityDragon TFT data. Cached twice over:
    once on disk (so we don't re-download), once in memory (so we
    don't re-read and re-parse the file on every call).
    """
    global _in_memory_cache
    if _in_memory_cache is not None:
        return _in_memory_cache

    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _in_memory_cache = json.load(f)
            return _in_memory_cache

    print("Downloading fresh game data from CommunityDragon...")
    response = requests.get(CDRAGON_URL)
    response.raise_for_status()
    data = response.json()

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)

    _in_memory_cache = data
    return data


def build_champion_lookup(set_number: int = 18) -> dict:
    """
    Returns a dict mapping character_id (e.g. 'DA_18_Ezreal') to
    a readable name (e.g. 'Ezreal'), for the given set.
    """
    data = _load_raw_data()
    champions = data["sets"][str(set_number)]["champions"]
    return {champ["apiName"]: champ["name"] for champ in champions}


def build_item_lookup() -> dict:
    """
    Returns a dict mapping item apiName (e.g. 'DA_GargoyleStoneplate')
    to a readable name. Items aren't split by set in this data.
    """
    data = _load_raw_data()
    return {item["apiName"]: item["name"] for item in data["items"]}

def translate_items(items_string: str, item_lookup: dict) -> str:
    """
    Takes a comma-joined string of raw item IDs (e.g. 'DA_GargoyleStoneplate, DA_SpiritVisage')
    and returns the same thing with readable names instead.
    """
    if not items_string:
        return ""

    raw_ids = items_string.split(", ")  # undo the join we did when saving
    readable_names = [item_lookup.get(raw_id, raw_id) for raw_id in raw_ids]
    return ", ".join(readable_names)

def translate_items(items_string: str, item_lookup: dict) -> str:
    """
    Takes a comma-joined string of raw item IDs (e.g. 'DA_GargoyleStoneplate, DA_SpiritVisage')
    and returns the same thing with readable names instead.
    """
    if not items_string:
        return ""

    raw_ids = items_string.split(", ")
    readable_names = [item_lookup.get(raw_id, raw_id) for raw_id in raw_ids]
    return ", ".join(readable_names)

CDRAGON_BASE = "https://raw.communitydragon.org/latest/"
MIRROR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "cdragon")


def mirrored_url(url: str) -> str:
    """
    Returns the locally served copy of a CommunityDragon image (see
    sync_assets.py) if it has been downloaded, otherwise the original URL.
    """
    rel = url.removeprefix(CDRAGON_BASE)
    if rel != url and os.path.exists(os.path.join(MIRROR_DIR, *rel.split("/"))):
        return "/app/static/cdragon/" + rel
    return url


def tex_path_to_url(path: str) -> str:
    """
    Converts a raw game asset path (e.g. 'assets/characters/.../icon.tex')
    into an actual loadable image URL from CommunityDragon.
    """
    converted = path.lower().replace(".tex", ".png")
    return mirrored_url(f"{CDRAGON_BASE}game/{converted}")


def build_champion_data(set_number: int = 18) -> dict:
    """
    Like build_champion_lookup, but returns the FULL champion dict
    (name, cost, squareIcon, etc.) instead of just the name.
    """
    data = _load_raw_data()
    champions = data["sets"][str(set_number)]["champions"]
    return {champ["apiName"]: champ for champ in champions}

COMPANIONS_CACHE_FILE = "companions_data_cache.json"
_companions_in_memory_cache = None

def build_companion_lookup() -> dict:
    """
    Downloads (and caches) Riot's companion (Little Legend) data,
    keyed by contentId to match the 'content_ID' field in our own
    stored match data.
    """
    global _companions_in_memory_cache
    if _companions_in_memory_cache is not None:
        return _companions_in_memory_cache

    if os.path.exists(COMPANIONS_CACHE_FILE):
        with open(COMPANIONS_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        print("Downloading fresh companion data...")
        url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/companions.json"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        with open(COMPANIONS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

    lookup = {entry["contentId"]: entry for entry in data}
    _companions_in_memory_cache = lookup
    return lookup


def companion_icon_url(loadouts_icon_path: str) -> str:
    """
    Converts a companion's loadoutsIcon path (e.g.
    '/lol-game-data/assets/ASSETS/Loadouts/Companions/Foo.png')
    into an actual loadable URL. Different prefix rule than
    tex_path_to_url, since this comes from a different data source.
    """
    stripped = loadouts_icon_path.replace("/lol-game-data/assets/", "", 1)
    return mirrored_url(f"{CDRAGON_BASE}plugins/rcp-be-lol-game-data/global/default/{stripped.lower()}")

TRAIT_STATIC_CACHE_FILE = "trait_static_cache.json"
_trait_static_in_memory_cache = None

def build_trait_static_data(set_name: str = "TFTSet18") -> dict:
    """
    Downloads (and caches) Riot's trait tooltip data, which includes
    resolved numeric constants per breakpoint — unlike the champion
    data, which never has these. Keyed by trait_id.
    """
    global _trait_static_in_memory_cache
    if _trait_static_in_memory_cache is not None:
        return _trait_static_in_memory_cache

    if os.path.exists(TRAIT_STATIC_CACHE_FILE):
        with open(TRAIT_STATIC_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        print("Downloading fresh trait tooltip data...")
        url = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/tfttraits.json"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        with open(TRAIT_STATIC_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

    lookup = {t["trait_id"]: t for t in data if t.get("set") == set_name}
    _trait_static_in_memory_cache = lookup
    return lookup


def build_item_data() -> dict:
    """
    Same idea for items — full dict, so we can get the icon path.
    """
    data = _load_raw_data()
    return {item["apiName"]: item for item in data["items"]}

def build_trait_data(set_number: int = 18) -> dict:
    data = _load_raw_data()
    traits = data["sets"][str(set_number)]["traits"]
    return {t["apiName"]: t for t in traits}