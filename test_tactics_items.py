from tactics_tools_items import fetch_plain_text, parse_items
from game_data import build_item_data

item_data = build_item_data()
known_names = {item["name"] for item in item_data.values()}

text = fetch_plain_text()
items = parse_items(text, known_names)

print(f"Parsed {len(items)} of {len(known_names)} known items.\n")

for name in ("Gargoyle Stoneplate", "Bloodthirster", "Deathblade", "Infinity Edge"):
    entry = items.get(name)
    print(f"--- {name} ---")
    print(entry["description"] if entry else "NOT FOUND")
    print()