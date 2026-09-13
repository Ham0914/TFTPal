from wiki_ability_data import build_ability_lookup, resolve_ability_text

lookup = build_ability_lookup({"DA_18_Ezreal": "Forest's Flurry"})
entry = lookup.get("DA_18_Ezreal")

if entry:
    print("Stats:", entry["hp"], entry["mana"], entry["ad"])
    for star in (1, 2, 3):
        print(f"\n{star}-star:")
        print(resolve_ability_text(entry["raw_active"], star))
else:
    print("No match found — matching logic needs adjusting.")