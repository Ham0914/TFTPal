import sqlite3
from tactics_tools_items import fetch_plain_text, parse_items
from game_data import build_item_data

conn = sqlite3.connect("tft_coach.db")
raw_items = conn.execute("SELECT DISTINCT items FROM units WHERE items != ''").fetchall()
conn.close()

item_ids = set()
for (items_str,) in raw_items:
    for item_id in items_str.split(", "):
        if item_id:
            item_ids.add(item_id)

item_data = build_item_data()
real_names = {item_data[iid]["name"] for iid in item_ids if iid in item_data}

text = fetch_plain_text()
parsed = parse_items(text, real_names)

missing = real_names - set(parsed.keys())
print(f"Items you've actually equipped this set: {len(real_names)}")
print(f"Successfully parsed: {len(parsed)}")
print(f"\nMissing: {missing}")