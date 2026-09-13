from tactics_tools_data import fetch_plain_text, parse_champions

text = fetch_plain_text()
champions = parse_champions(text)

print(f"Parsed {len(champions)} champions total.\n")

for name in ("Ezreal", "Morgana", "Sentinel", "Veigar"):
    c = champions.get(name)
    if not c:
        print(f"{name}: NOT FOUND")
        continue
    print(f"--- {name} ({c['cost']} cost) ---")
    print(f"Ability: {c['ability_name']}  Mana: {c['start_mana']}/{c['max_mana']}")
    print(c["description"])
    print()