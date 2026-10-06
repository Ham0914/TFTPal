import json

from game_data import build_champion_data, build_item_data, build_trait_data, build_trait_static_data, tex_path_to_url

MAX_ITEMS_PER_UNIT = 3


def build_team_builder_payload(set_number: int = 18) -> dict:
    champs_raw = build_champion_data(set_number)
    trait_data = build_trait_data(set_number)
    trait_static = build_trait_static_data(f"TFTSet{set_number}")
    name_to_trait_id = {t["name"]: trait_id for trait_id, t in trait_data.items()}

    traits = []
    for trait_id, t in trait_data.items():
        breakpoints = [b["min_units"] for b in (trait_static.get(trait_id) or {}).get("conditional_trait_sets", [])]
        if not breakpoints:
            continue
        traits.append({
            "id": trait_id,
            "name": t["name"],
            "icon": tex_path_to_url(t["icon"]) if t.get("icon") else None,
            "breakpoints": breakpoints,
        })

    champions = []
    for api_name, c in champs_raw.items():
        if not c.get("cost") or not c.get("traits") or not c.get("squareIcon") or c["squareIcon"] == "None":
            continue
        champions.append({
            "id": api_name,
            "name": c["name"],
            "cost": c["cost"],
            "traits": [name_to_trait_id.get(tn, tn) for tn in c["traits"]],
            "icon": tex_path_to_url(c["squareIcon"]),
        })

    components, completed = [], []
    for api_name, it in build_item_data().items():
        if not it.get("icon"):
            continue
        entry = {"id": api_name, "name": it["name"], "icon": tex_path_to_url(it["icon"])}
        if api_name.startswith("DA_Component_"):
            components.append(entry)
        elif (api_name.startswith("DA_") and "Upgrade" not in api_name and "Emblem" not in api_name
              and not it.get("isAugment")
              and len(it.get("composition") or []) == 2
              and all(p.startswith("DA_Component_") for p in it["composition"])):
            # composition is the recipe: the two components that combine into this item
            completed.append({**entry, "composition": list(it["composition"])})

    return {
        "champions": sorted(champions, key=lambda c: (c["cost"], c["name"])),
        "traits": sorted(traits, key=lambda t: t["name"]),
        "components": sorted(components, key=lambda i: i["name"]),
        "completed": sorted(completed, key=lambda i: i["name"]),
        "maxItems": MAX_ITEMS_PER_UNIT,
    }


def build_team_builder_html(set_number: int = 18) -> str:
    payload_json = json.dumps(build_team_builder_payload(set_number), ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__PAYLOAD__", payload_json)


TEMPLATE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #0a0c10; color: #ddd; font-family: "Segoe UI", system-ui, sans-serif; font-size: 13px; overflow: hidden; }
  .frame { display: grid; grid-template-columns: 240px 1fr 220px; grid-template-rows: auto minmax(0,1fr) 270px; gap: 10px; padding: 10px; height: 760px; }
  .panel { background: linear-gradient(180deg, rgba(20,23,31,0.95), rgba(14,16,21,0.95)); border: 1px solid #2e323d; border-radius: 10px; padding: 10px; overflow: hidden; }
  .topbar { grid-column: 1 / 4; display: flex; align-items: center; gap: 8px; }
  .topbar .title { font-weight: 800; letter-spacing: 1px; color: #fff; margin-right: auto; font-size: 14px; }
  .btn { background: #14171f; border: 1px solid #2e323d; color: #ddd; border-radius: 7px; padding: 4px 10px; cursor: pointer; font-weight: 600; font-size: 12px; }
  .btn:hover { border-color: #d4af37; color: #d4af37; }
  .section-label { color: #999; font-size: 10px; letter-spacing: 1px; text-transform: uppercase; margin: 2px 0 6px; }

  /* Traits */
  .trait-row { display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-radius: 7px; margin-bottom: 3px; background: rgba(255,255,255,0.02); }
  .trait-row img { width: 20px; height: 20px; border-radius: 50%; border: 2px solid #555; }
  .trait-row .tname { flex: 1; min-width: 0; font-weight: 600; color: #fff; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .trait-row .tbp { font-size: 10px; color: #666; white-space: nowrap; }
  .trait-row .tbp b { color: #fff; }

  /* Board */
  .board { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; position: relative; }
  .hex-row { display: flex; gap: 5px; }
  .hex-row + .hex-row { margin-top: -21px; }
  .hex-row.offset { margin-left: 42px; }
  .hex-wrap { position: relative; width: 76px; height: 86px; }
  .hex { position: absolute; inset: 0; clip-path: polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%); background: #2e323d; transition: background 0.15s; }
  .hex.over { background: #d4af37; }
  .hex-inner { position: absolute; inset: 3px; clip-path: polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%); background: #14171f; overflow: hidden; display: flex; align-items: center; justify-content: center; }
  .hex-inner img.unit { width: 100%; height: 100%; object-fit: cover; display: block; }
  .hex-items { position: absolute; left: 0; right: 0; bottom: -18px; display: flex; justify-content: center; gap: 3px; }
  .hex-items img { width: 18px; height: 18px; border-radius: 4px; border: 1px solid #555; background: #14171f; cursor: pointer; }
  .hex-items img:hover { border-color: #e05a5a; }
  .board-title { position: absolute; top: 2px; left: 0; color: #666; font-size: 11px; letter-spacing: 1px; }
  .toast { position: absolute; top: 4px; left: 50%; transform: translateX(-50%); background: rgba(192,57,43,0.9); color: #fff; padding: 6px 14px; border-radius: 8px; font-weight: 600; opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 5; }
  .toast.show { opacity: 1; }

  /* Right side */
  .alt-hex { display: inline-block; width: 52px; height: 58px; background: #14171f; border: 1px solid #2e323d; clip-path: polygon(50% 0%, 100% 25%, 100% 75%, 50% 100%, 0% 75%, 0% 25%); margin: 0 8px 8px 0; }

  /* Bench: 70% champions, 30% items */
  .bench { grid-column: 1 / 4; display: grid; grid-template-columns: 7fr 3fr; gap: 12px; min-height: 0; }
  .bench-col { display: flex; flex-direction: column; gap: 8px; min-height: 0; min-width: 0; }
  .bench-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .tab { padding: 3px 10px; border-radius: 14px; border: 1px solid #2e323d; cursor: pointer; color: #bbb; font-weight: 600; font-size: 12px; background: transparent; }
  .tab.on { background: #d4af37; color: #111; border-color: #d4af37; }
  .search { margin-left: auto; background: #14171f; border: 1px solid #2e323d; color: #ddd; border-radius: 7px; padding: 4px 8px; width: 170px; font-size: 12px; }
  .bench-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 4px; }
  .champ-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(72px, 1fr)); grid-auto-rows: 80px; gap: 7px; }
  .champ { position: relative; border-radius: 7px; border: 2px solid #555; overflow: hidden; cursor: grab; background: #14171f; }
  .champ img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .champ .cname { position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0,0,0,0.7); color: #fff; font-size: 10px; font-weight: 600; text-align: center; padding: 1px 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .items-scroll { display: flex; flex-direction: column; gap: 10px; }
  .comp-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(44px, 1fr)); grid-auto-rows: 44px; gap: 6px; }
  .comp-item { width: 100%; height: 100%; border-radius: 7px; border: 1px solid #555; background: #14171f; cursor: grab; object-fit: cover; }
  .craft-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(84px, 1fr)); grid-auto-rows: 80px; gap: 7px; }
  .item-card { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; cursor: grab; border-radius: 7px; padding: 3px; background: rgba(255,255,255,0.02); }
  .item-card img { width: 40px; height: 40px; border-radius: 6px; border: 1px solid #555; background: #14171f; }
  .item-card span { font-size: 9px; color: #bbb; text-align: center; line-height: 1.15; }
</style></head>
<body>
<div class="frame">
  <div class="topbar">
    <span class="title">Set 18 · Team Builder</span>
    <button class="btn" id="undo">Undo</button>
    <button class="btn" id="redo">Redo</button>
    <button class="btn" id="clear">Clear</button>
  </div>

  <div class="panel" id="traitsPanel">
    <div class="section-label">Traits</div>
    <div id="traits"></div>
  </div>

  <div class="panel board" id="boardPanel">
    <div class="board-title">Board</div>
    <div class="toast" id="toast"></div>
    <div id="boardRows"></div>
  </div>

  <div class="panel" id="rightPanel">
    <div class="section-label">Augments</div>
    <div><span class="alt-hex"></span><span class="alt-hex"></span><span class="alt-hex"></span></div>
    <div class="section-label" style="margin-top:14px">Alt Builds</div>
    <div><span class="alt-hex"></span><span class="alt-hex"></span></div>
  </div>

  <div class="panel bench" id="bench">
    <div class="bench-col" id="champCol">
      <div class="bench-head">
        <button class="tab" data-sub="cost">Cost</button>
        <button class="tab" data-sub="name">Name</button>
        <button class="tab" data-sub="origin">Origin</button>
        <input class="search" id="search" placeholder="Search champions…">
      </div>
      <div class="bench-scroll"><div class="champ-grid" id="champGrid"></div></div>
    </div>

    <div class="bench-col" id="itemCol">
      <div class="bench-head"><span class="section-label" style="margin:0">Items · drag onto a unit · click an item on a unit to remove it</span></div>
      <div class="bench-scroll items-scroll">
        <div class="section-label">Components</div>
        <div class="comp-grid" id="components"></div>
        <div class="section-label">Craftables</div>
        <div class="craft-grid" id="craftables"></div>
      </div>
    </div>
  </div>
</div>

<script>
const DATA = __PAYLOAD__;
const MAX_ITEMS = DATA.maxItems;
const COST_COLORS = {1:"#a0a0a0",2:"#1e9e4a",3:"#3a7bd5",4:"#a04dcf",5:"#e0b040"};
const TIER_COLORS = ["#a0673c","#9fa8b0","#d4af37","#4fd1c5"];
const CHAMP = Object.fromEntries(DATA.champions.map(c => [c.id, c]));
const TRAIT = Object.fromEntries(DATA.traits.map(t => [t.id, t]));
const ITEM = Object.fromEntries([...DATA.components, ...DATA.completed].map(i => [i.id, i]));
const COMPONENT_IDS = new Set(DATA.components.map(i => i.id));

// Recipe lookup: two components (order-independent) -> finished item
function pairKey(a, b) { return [a, b].sort().join("|"); }
const COMBO = {};
DATA.completed.forEach(i => { if (i.composition && i.composition.length === 2) COMBO[pairKey(i.composition[0], i.composition[1])] = i.id; });

const ROWS = [7,7,7,7];
const SLOT_COUNT = ROWS.reduce((a,b)=>a+b,0);
let board = Array.from({length: SLOT_COUNT}, () => null); // {id, items:[]} or null
let undoStack = [], redoStack = [];
let champSort = "cost";

// ---- history ----
function snapshot() { undoStack.push(JSON.stringify(board)); if (undoStack.length > 100) undoStack.shift(); redoStack = []; }
function restore(s) { board = JSON.parse(s); render(); }
document.getElementById("undo").onclick = () => { if (!undoStack.length) return; redoStack.push(JSON.stringify(board)); restore(undoStack.pop()); };
document.getElementById("redo").onclick = () => { if (!redoStack.length) return; undoStack.push(JSON.stringify(board)); restore(redoStack.pop()); };
document.getElementById("clear").onclick = () => { if (board.every(s => !s)) return; snapshot(); board = board.map(() => null); render(); };

// ---- toast ----
let toastTimer = null;
function flash(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 1600);
}

// ---- board layout ----
function buildBoard() {
  const wrap = document.getElementById("boardRows");
  wrap.innerHTML = "";
  let idx = 0;
  ROWS.forEach((count, r) => {
    const row = document.createElement("div");
    row.className = "hex-row" + (r % 2 ? " offset" : "");
    for (let c = 0; c < count; c++, idx++) {
      const slot = idx;
      const cell = document.createElement("div");
      cell.className = "hex-wrap";
      cell.innerHTML = '<div class="hex"></div><div class="hex-inner"></div><div class="hex-items"></div>';
      cell.dataset.slot = slot;
      cell.addEventListener("dragover", e => { e.preventDefault(); cell.querySelector(".hex").classList.add("over"); });
      cell.addEventListener("dragleave", () => cell.querySelector(".hex").classList.remove("over"));
      cell.addEventListener("drop", e => { e.preventDefault(); cell.querySelector(".hex").classList.remove("over"); onBoardDrop(slot, e); });
      row.appendChild(cell);
    }
    wrap.appendChild(row);
  });
}

function renderBoard() {
  document.querySelectorAll(".hex-wrap").forEach(cell => {
    const slot = +cell.dataset.slot;
    const s = board[slot];
    const inner = cell.querySelector(".hex-inner");
    const hex = cell.querySelector(".hex");
    const items = cell.querySelector(".hex-items");
    if (s) {
      const c = CHAMP[s.id];
      hex.style.background = COST_COLORS[c.cost];
      inner.innerHTML = `<img class="unit" src="${c.icon}" draggable="true" title="${c.name}">`;
      inner.querySelector("img").addEventListener("dragstart", e =>
        e.dataTransfer.setData("text/plain", JSON.stringify({kind:"move", from: slot})));
      items.innerHTML = s.items.map((id, i) =>
        `<img src="${ITEM[id].icon}" title="${ITEM[id].name} (click to remove)" draggable="true" data-i="${i}">`).join("");
      items.querySelectorAll("img").forEach(im => {
        const i = +im.dataset.i;
        im.addEventListener("dragstart", e => {
          e.stopPropagation();
          e.dataTransfer.setData("text/plain", JSON.stringify({kind:"unitItem", from: slot, idx: i, id: s.items[i]}));
        });
        im.addEventListener("click", e => { e.stopPropagation(); removeItem(slot, i); });
      });
    } else {
      hex.style.background = "#2e323d";
      inner.innerHTML = "";
      items.innerHTML = "";
    }
  });
}

// ---- traits panel ----
function renderTraits() {
  const counts = {};
  board.forEach(s => { if (!s) return; CHAMP[s.id].traits.forEach(t => counts[t] = (counts[t]||0) + 1); });
  const rows = Object.entries(counts).map(([id, n]) => {
    const t = TRAIT[id]; if (!t) return null;
    let tier = -1; t.breakpoints.forEach((b, i) => { if (n >= b) tier = i; });
    return {t, n, tier};
  }).filter(Boolean).sort((a,b) => (b.tier - a.tier) || (b.n - a.n));
  const el = document.getElementById("traits");
  el.innerHTML = rows.length ? rows.map(r => {
    const color = r.tier >= 0 ? TIER_COLORS[Math.min(r.tier, TIER_COLORS.length-1)] : "#555";
    const bps = r.t.breakpoints.map(b => (r.n >= b) ? `<b>${b}</b>` : `${b}`).join(" › ");
    return `<div class="trait-row">
      ${r.t.icon ? `<img src="${r.t.icon}" style="border-color:${color}">` : ""}
      <span class="tname" style="color:${r.tier>=0?"#fff":"#777"}">${r.t.name} <span style="color:${color}">${r.n}</span></span>
      <span class="tbp">${bps}</span></div>`;
  }).join("") : '<div style="color:#666">Place champions to see traits.</div>';
}

// ---- item logic ----
// Adds itemId to the unit in targetSlot. src is null (dragged from the items panel)
// or {slot, idx} (dragged off another unit). If the unit already holds a component
// that pairs with the dropped component, the two are combined into the finished item.
function applyItemDrop(targetSlot, itemId, src) {
  const target = board[targetSlot];
  if (!target) { flash("Place a champion there first"); return; }
  if (src && src.slot === targetSlot) return;

  const items = target.items.slice();
  let combineAt = -1;
  if (COMPONENT_IDS.has(itemId)) {
    combineAt = items.findIndex(x => COMPONENT_IDS.has(x) && COMBO[pairKey(x, itemId)]);
  }
  if (combineAt < 0 && items.length >= MAX_ITEMS) { flash(`Max ${MAX_ITEMS} items per champion`); return; }

  snapshot();
  if (src) board[src.slot].items.splice(src.idx, 1);
  if (combineAt >= 0) {
    const result = COMBO[pairKey(items[combineAt], itemId)];
    items.splice(combineAt, 1);
    items.push(result);
  } else {
    items.push(itemId);
  }
  target.items = items;
  render();
}

function removeItem(slot, idx) {
  if (!board[slot]) return;
  snapshot();
  board[slot].items.splice(idx, 1);
  render();
}

// ---- items panel ----
function renderItemPanels() {
  document.getElementById("components").innerHTML = DATA.components.map(i =>
    `<img class="comp-item" src="${i.icon}" title="${i.name}" draggable="true" data-id="${i.id}">`).join("");
  document.getElementById("craftables").innerHTML = DATA.completed.map(i =>
    `<div class="item-card" draggable="true" data-id="${i.id}" title="${i.name}"><img src="${i.icon}"><span>${i.name}</span></div>`).join("");
  document.querySelectorAll("#components .comp-item, #craftables .item-card").forEach(el =>
    el.addEventListener("dragstart", e => e.dataTransfer.setData("text/plain", JSON.stringify({kind:"item", id: el.dataset.id}))));
}

// ---- champion bench ----
function renderChampions() {
  document.querySelectorAll(".tab[data-sub]").forEach(b => b.classList.toggle("on", b.dataset.sub === champSort));
  const q = document.getElementById("search").value.trim().toLowerCase();
  let list = DATA.champions.filter(c => !q || c.name.toLowerCase().includes(q));
  if (champSort === "name") list.sort((a,b) => a.name.localeCompare(b.name));
  else if (champSort === "origin") list.sort((a,b) => (TRAIT[a.traits[0]]?.name||"").localeCompare(TRAIT[b.traits[0]]?.name||"") || a.name.localeCompare(b.name));
  else list.sort((a,b) => a.cost - b.cost || a.name.localeCompare(b.name));
  const grid = document.getElementById("champGrid");
  grid.innerHTML = list.map(c => `<div class="champ" draggable="true" data-id="${c.id}" style="border-color:${COST_COLORS[c.cost]}">
    <img src="${c.icon}"><div class="cname">${c.name}</div></div>`).join("");
  grid.querySelectorAll(".champ").forEach(el => el.addEventListener("dragstart", e =>
    e.dataTransfer.setData("text/plain", JSON.stringify({kind:"champ", id: el.dataset.id}))));
}

document.querySelectorAll(".tab[data-sub]").forEach(b => b.onclick = () => { champSort = b.dataset.sub; renderChampions(); });
document.getElementById("search").addEventListener("input", renderChampions);

// ---- removal drop zones ----
function parseDrag(e) { try { return JSON.parse(e.dataTransfer.getData("text/plain")); } catch { return null; } }

// Champion column: dropping a board unit or a unit's item here removes it
const champCol = document.getElementById("champCol");
champCol.addEventListener("dragover", e => e.preventDefault());
champCol.addEventListener("drop", e => {
  e.preventDefault();
  const d = parseDrag(e); if (!d) return;
  if (d.kind === "move") { snapshot(); board[d.from] = null; render(); }
  else if (d.kind === "unitItem") removeItem(d.from, d.idx);
});

// Item column: dropping a unit's item here removes it from the unit
const itemCol = document.getElementById("itemCol");
itemCol.addEventListener("dragover", e => e.preventDefault());
itemCol.addEventListener("drop", e => {
  e.preventDefault();
  const d = parseDrag(e); if (!d) return;
  if (d.kind === "unitItem") removeItem(d.from, d.idx);
});

// ---- board drops ----
function onBoardDrop(slot, e) {
  const d = parseDrag(e); if (!d) return;
  if (d.kind === "champ") {
    if (board[slot]) { flash("Slot is occupied, drag that unit off first"); return; }
    snapshot(); board[slot] = {id: d.id, items: []}; render();
  } else if (d.kind === "move") {
    if (d.from === slot) return;
    snapshot(); [board[d.from], board[slot]] = [board[slot], board[d.from]]; render();
  } else if (d.kind === "item") {
    applyItemDrop(slot, d.id, null);
  } else if (d.kind === "unitItem") {
    applyItemDrop(slot, d.id, {slot: d.from, idx: d.idx});
  }
}

function render() { renderBoard(); renderTraits(); }

buildBoard();
renderItemPanels();
renderChampions();
render();
</script>
</body></html>
"""
