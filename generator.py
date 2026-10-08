"""Rule-based eBay listing generator (v2).

generate_listing(details) -> {
    title, title_chars, trimmed,
    specifics[{name, value}],
    description,
    shipping_returns,          # copy-paste block for eBay's shipping/returns form
    readiness[{label, status, note}],  # ok | warn | missing
    tips[]
}

Covers what eBay's listing form actually requires: title, category, price,
quantity, condition, item specifics, photos, handling time, shipping,
returns, and ship-from location. No LLM, no API calls.
"""
import re

TITLE_LIMIT = 80

CONDITIONS = [
    ("new", "New"),
    ("open_box", "Open box"),
    ("refurbished", "Certified - Refurbished"),
    ("used_excellent", "Used - Excellent"),
    ("used_good", "Used - Good"),
    ("for_parts", "For parts or not working"),
]

# Appended to the title (eBay's condition field covers the rest).
CONDITION_PHRASES = {
    "new": "",
    "open_box": "Open Box",
    "refurbished": "Certified Refurbished",
    "used_excellent": "Excellent Condition",
    "used_good": "Good Condition",
    "for_parts": "For Parts Only",
}

HANDLING_LABELS = {
    "1": "1 business day",
    "2": "2 business days",
    "3": "3 business days",
    "5": "5 business days",
}

SHIPPING_LABELS = {
    "free": "Free shipping",
    "flat": "Flat-rate shipping",
    "calculated": "Calculated shipping",
}

RETURNS_LABELS = {
    "30": "30-day returns accepted",
    "14": "14-day returns accepted",
    "none": "No returns accepted",
}


def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def _split_lines(s):
    return [_clean(x) for x in (s or "").replace(",", "\n").split("\n") if _clean(x)]


def generate_listing(d):
    name = _clean(d.get("product_name"))
    if not name:
        raise ValueError("Product name is required.")
    brand = _clean(d.get("brand"))
    model = _clean(d.get("model"))
    storage = _clean(d.get("storage"))
    color = _clean(d.get("color"))
    cond_key = (d.get("condition") or "new").strip()
    cond_label = dict(CONDITIONS).get(cond_key, "New")
    features = _split_lines(d.get("features"))[:5]
    included = _split_lines(d.get("included"))[:8]

    # ---- eBay-required listing details -----------------------------------
    category = _clean(d.get("category"))
    price = _clean(d.get("price")).lstrip("$").strip()
    try:
        quantity = max(1, int(_clean(d.get("quantity")) or 1))
    except (TypeError, ValueError):
        quantity = 1
    handling = HANDLING_LABELS.get(_clean(d.get("handling_time")))
    ship_key = _clean(d.get("shipping_type"))
    ship_label = SHIPPING_LABELS.get(ship_key)
    ship_cost = _clean(d.get("shipping_cost")).lstrip("$").strip()
    returns_label = RETURNS_LABELS.get(_clean(d.get("returns_option")))
    ships_from = _clean(d.get("ships_from"))
    pkg_weight = _clean(d.get("package_weight"))

    # ---- title: Brand + Name + Model + attrs + features + condition --------
    parts, seen = [], set()

    def add(p):
        p = _clean(p)
        if not p:
            return
        # drop individual words already in the title (keeps original casing)
        new_words = [w for w in p.split() if w.lower() not in seen]
        if not new_words:
            return
        parts.append(" ".join(new_words))
        seen.update(w.lower() for w in new_words)

    add(brand)
    add(name)
    add(model)
    add(storage)
    add(color)
    for f in features:
        add(f)
    add(CONDITION_PHRASES.get(cond_key, ""))

    title = " ".join(parts)
    trimmed = False
    while len(title) > TITLE_LIMIT and len(parts) > 3:
        parts.pop()  # drop lowest-priority part first
        title = " ".join(parts)
        trimmed = True
    if len(title) > TITLE_LIMIT:  # last resort: hard cut, never mid-word
        title = title[:TITLE_LIMIT].rsplit(" ", 1)[0]
        trimmed = True

    # ---- item specifics --------------------------------------------------
    specifics = []
    if brand:
        specifics.append({"name": "Brand", "value": brand})
    if model:
        specifics.append({"name": "Model", "value": model})
    if color:
        specifics.append({"name": "Color", "value": color})
    if storage:
        specifics.append({"name": "Storage", "value": storage})
    specifics.append({"name": "Condition", "value": cond_label})

    # ---- description draft (only promises what the seller entered) ---------
    lines = [title, "", "Condition: %s." % cond_label]
    if features:
        lines += ["", "Key features:"] + ["\u2022 " + f for f in features]
    if included:
        lines += ["", "What's included:"] + ["\u2022 " + x for x in included]
    ship_lines = []
    if handling:
        ship_lines.append("Ships within %s with tracking." % handling)
    if ships_from:
        ship_lines.append("Ships from %s." % ships_from)
    if returns_label:
        ship_lines.append("Returns: %s." % returns_label.lower())
    if ship_lines:
        lines += [""] + ship_lines
    description = "\n".join(lines)

    # ---- shipping & returns copy block ------------------------------------
    sr = []
    if handling:
        sr.append("Handling time: %s" % handling)
    if ship_label:
        s = ship_label
        if ship_key == "flat" and ship_cost:
            s += " ($%s)" % ship_cost
        sr.append("Shipping: %s" % s)
    if ships_from:
        sr.append("Ships from: %s" % ships_from)
    if pkg_weight:
        sr.append("Package weight: %s" % pkg_weight)
    if returns_label:
        sr.append("Returns: %s" % returns_label)
    shipping_returns = "\n".join(sr)

    # ---- listing readiness checklist ---------------------------------------
    readiness = []
    readiness.append({"label": "Title", "status": "ok",
                      "note": "%d/%d characters used." % (len(title), TITLE_LIMIT)})
    readiness.append({"label": "Price", "status": "ok" if price else "missing",
                      "note": ("$%s \u00d7 %d" % (price, quantity)) if price
                      else "eBay won't let you list without a price."})
    readiness.append({"label": "Category", "status": "ok" if category else "warn",
                      "note": category if category
                      else "Pick the most specific category on eBay \u2014 wrong category hides your listing and changes fees."})
    readiness.append({"label": "Photos", "status": "warn",
                      "note": "Upload 8\u201312 clear photos: front, back, angles, close-up of any flaws, and everything included."})
    readiness.append({"label": "Item specifics", "status": "ok",
                      "note": "%d drafted \u2014 fill every specific eBay suggests; filtered search is where sales happen." % len(specifics)})
    readiness.append({"label": "Description", "status": "ok",
                      "note": "Draft ready below \u2014 add measurements or compatibility notes if they matter for this item."})
    readiness.append({"label": "Handling time", "status": "ok" if handling else "warn",
                      "note": handling if handling
                      else "Set it on eBay \u2014 late shipment hurts your seller rating."})
    readiness.append({"label": "Shipping", "status": "ok" if ship_label else "warn",
                      "note": (ship_label + (" ($%s)" % ship_cost if ship_key == "flat" and ship_cost else ""))
                      if ship_label else "Free shipping wins the buy box; flat rate is fine for heavy items."})
    readiness.append({"label": "Returns", "status": "ok" if returns_label else "warn",
                      "note": returns_label if returns_label
                      else "30-day returns get a search boost on eBay."})

    # ---- tips --------------------------------------------------------------
    tips = []
    if len(title) < 60:
        tips.append(
            "Title uses %d/%d chars \u2014 add 1\u20132 more keywords "
            "(model number, size, bundle) to use the full space." % (len(title), TITLE_LIMIT))
    if trimmed:
        tips.append("Title was trimmed to fit %d chars \u2014 dropped words were "
                    "lowest priority; the important keywords survived." % TITLE_LIMIT)
    if not category:
        tips.append("Search your exact item on eBay, open a sold listing, and copy its category path \u2014 that's the category eBay expects.")
    if ship_key == "free":
        tips.append("With free shipping, bake the postage into your price \u2014 buyers filter for it.")
    if returns_label == "No returns accepted":
        tips.append("\u201cNo returns\u201d lowers buyer confidence \u2014 describe the condition precisely and photograph every flaw.")
    if not features:
        tips.append("Add 2\u20133 key features above and regenerate \u2014 specifics sell the click.")

    return {"title": title, "title_chars": len(title), "trimmed": trimmed,
            "specifics": specifics, "description": description,
            "shipping_returns": shipping_returns, "readiness": readiness,
            "tips": tips}
