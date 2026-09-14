import os
import game_data
from game_data import CACHE_FILE, COMPANIONS_CACHE_FILE, TRAIT_STATIC_CACHE_FILE, _load_raw_data
from patch_tracker import take_snapshot
import metatft_data


for path in (CACHE_FILE, COMPANIONS_CACHE_FILE, TRAIT_STATIC_CACHE_FILE):
    if os.path.exists(path):
        os.remove(path)
        print(f"Deleted stale cache: {path}")

metatft_data._in_memory_cache = None
metatft_data.fetch_metatft_data(force_refresh=True)
print("Refreshed metatft champion/item data.")

game_data._in_memory_cache = None
game_data._companions_in_memory_cache = None
game_data._trait_static_in_memory_cache = None

data = _load_raw_data()
print(f"Re-downloaded. Champions in set 18: {len(data['sets']['18']['champions'])}")

print(take_snapshot())
