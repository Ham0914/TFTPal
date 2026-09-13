import os
import game_data
from game_data import CACHE_FILE, COMPANIONS_CACHE_FILE, TRAIT_STATIC_CACHE_FILE, _load_raw_data
from patch_tracker import take_snapshot
from wiki_ability_data import fetch_raw_lua
import wiki_ability_data
import tactics_tools_data

for path in (CACHE_FILE, COMPANIONS_CACHE_FILE, TRAIT_STATIC_CACHE_FILE):
    if os.path.exists(path):
        os.remove(path)
        print(f"Deleted stale cache: {path}")

wiki_ability_data._full_lookup_cache = None
fetch_raw_lua(force_refresh=True)
print("Refreshed wiki ability data.")

tactics_tools_data._in_memory_cache = None
fetch_tactics_text_result = tactics_tools_data.fetch_plain_text(force_refresh=True)
print("Refreshed tactics.tools ability data.")

game_data._in_memory_cache = None
game_data._companions_in_memory_cache = None
game_data._trait_static_in_memory_cache = None

data = _load_raw_data()
print(f"Re-downloaded. Champions in set 18: {len(data['sets']['18']['champions'])}")

print(take_snapshot())