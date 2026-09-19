import sqlite3
from game_data import build_champion_data, build_item_data
from metatft_data import build_metatft_champion_lookup, build_metatft_item_lookup, build_metatft_item_name_lookup

conn = sqlite3.connect("tftpal.db")
character_ids = {row[0] for row in conn.execute("SELECT DISTINCT character_id FROM units")}
raw_items = conn.execute("SELECT DISTINCT items FROM units WHERE items != ''").fetchall()
conn.close()

item_ids = set()
for (items_str,) in raw_items:
    for item_id in items_str.split(", "):
        if item_id:
            item_ids.add(item_id)

champion_data = build_champion_data()
item_data = build_item_data()
metatft_champions = build_metatft_champion_lookup(champion_data)
metatft_items = build_metatft_item_lookup()
metatft_item_names = build_metatft_item_name_lookup()

matched_champs = [cid for cid in character_ids if cid in metatft_champions]
missing_champs = [cid for cid in character_ids if cid not in metatft_champions]


def item_matches(item_id):
    if item_id in metatft_items:
        return True
    name = item_data.get(item_id, {}).get("name")
    return bool(name and name.lower() in metatft_item_names)


matched_items = [iid for iid in item_ids if item_matches(iid)]
missing_items = [iid for iid in item_ids if not item_matches(iid)]
missing_item_names = [item_data.get(iid, {}).get("name", iid) for iid in missing_items]

print(f"Champions seen in real matches: {len(character_ids)}")
print(f"  Matched to metatft: {len(matched_champs)}")
print(f"  Missing: {missing_champs}")
print()
print(f"Items seen in real matches: {len(item_ids)}")
print(f"  Matched to metatft (ID or name): {len(matched_items)}")
print(f"  Missing: {missing_item_names}")
