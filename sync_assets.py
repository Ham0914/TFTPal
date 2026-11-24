"""
Mirrors every CommunityDragon image the site uses into ./static/cdragon/ so the
UI keeps working when CommunityDragon is down.

Run after refresh_data.py (or whenever a patch adds new content):
    python sync_assets.py            # download anything missing
    python sync_assets.py --check    # just report what's missing, no downloads

Safe to re-run: files already on disk are skipped.
"""
import json
import os
import sys
import time

import requests

from game_data import CDRAGON_BASE

CACHE_FILES = [
    "cdragon_data_cache.json",
    "companions_data_cache.json",
    "trait_static_cache.json",
]
PLUGIN_BASE = CDRAGON_BASE + "plugins/rcp-be-lol-game-data/global/default/"
LOCAL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "cdragon")
RANK_EMBLEM_BASE = CDRAGON_BASE + "plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/emblem-"
RANK_TIERS = ["iron", "bronze", "silver", "gold", "platinum", "emerald", "diamond", "master", "grandmaster", "challenger"]
REQUEST_PAUSE = 0.05
MAX_RETRIES = 3


def _asset_urls_from_value(value, out):
    """Walk any JSON structure and collect image paths as full CommunityDragon URLs."""
    if isinstance(value, dict):
        for v in value.values():
            _asset_urls_from_value(v, out)
    elif isinstance(value, list):
        for v in value:
            _asset_urls_from_value(v, out)
    elif isinstance(value, str):
        lower = value.lower()
        if not (lower.endswith((".tex", ".png", ".dds"))):
            return
        if value.startswith("/lol-game-data/assets/"):
            # Companion and trait icons live under the plugin path, same as companion_icon_url()
            out.add(PLUGIN_BASE + value[len("/lol-game-data/assets/"):].lower())
        elif lower.startswith("assets/"):
            out.add(CDRAGON_BASE + "game/" + lower.replace(".tex", ".png"))


def collect_urls() -> set:
    urls = {f"{RANK_EMBLEM_BASE}{tier}.png" for tier in RANK_TIERS}
    for name in CACHE_FILES:
        if not os.path.exists(name):
            print(f"  skipping {name} (not found; run refresh_data.py)")
            continue
        with open(name, "r", encoding="utf-8") as f:
            _asset_urls_from_value(json.load(f), urls)
    return urls


def local_path_for(url: str) -> str:
    """Maps a CommunityDragon URL to its mirrored file path under static/cdragon/."""
    rel = url.split("/latest/", 1)[1] if "/latest/" in url else url.rsplit("/", 1)[-1]
    return os.path.join(LOCAL_ROOT, *rel.split("/"))


def download(url: str, dest: str) -> bool:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                tmp = dest + ".part"
                with open(tmp, "wb") as f:
                    f.write(r.content)
                os.replace(tmp, dest)
                return True
            if r.status_code == 404:
                return False  # genuinely missing upstream; don't retry
        except requests.RequestException:
            pass
        time.sleep(2 * attempt)
    return False


def main():
    check_only = "--check" in sys.argv
    urls = sorted(collect_urls())
    missing = [u for u in urls if not os.path.exists(local_path_for(u))]
    print(f"{len(urls)} images referenced, {len(urls) - len(missing)} already mirrored, {len(missing)} to fetch")
    if check_only or not missing:
        return

    failed = []
    for i, url in enumerate(missing, 1):
        if not download(url, local_path_for(url)):
            failed.append(url)
        if i % 100 == 0:
            print(f"  {i}/{len(missing)}")
        time.sleep(REQUEST_PAUSE)

    print(f"done: {len(missing) - len(failed)} downloaded, {len(failed)} failed")
    total = sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(LOCAL_ROOT) for f in files)
    print(f"mirror size on disk: {total / (1024 ** 2):.0f} MB")
    if failed:
        with open(os.path.join(os.path.dirname(LOCAL_ROOT), "cdragon_failed.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(failed))
        print("failed URLs written to static/cdragon_failed.txt; re-run to retry them")


if __name__ == "__main__":
    main()
