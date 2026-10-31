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

from analyzer import _aspect_key

TITLE_LIMIT = 80

CONDITIONS = [
    ("new", "New"),
    ("used", "Used"),
    ("open_box", "Open box"),
    ("refurbished", "Certified - Refurbished"),
    ("used_excellent", "Used - Excellent"),
    ("used_good", "Used - Good"),
    ("for_parts", "For parts or not working"),
]

# Appended to the title (eBay's condition field covers the rest).
CONDITION_PHRASES = {
    "new": "",
    "used": "",
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


# Words that already state a condition — don't append a second, conflicting one.
CONDITION_WORDS = re.compile(
    r"\b(new|used|mint|excellent|good|fair|refurb\w*|renewed|pre-?owned|parts|"
    r"broken|faulty|open box|nwt|nib|bnib)\b", re.I)


def _tidy_title(t):
    """Remove emoji/symbols and dash/pipe separators buyers don't search for."""
    t = (t or "").replace("\U0001F50B", " Battery ")  # "100% 🔋" keeps its meaning
    t = re.sub(r"[^\w\s%&/+.,'\"()-]", " ", t)
    t = re.sub(r"(?<=\s)[-|~]+(?=\s)", " ", " %s " % t)
    return _clean(t)


def _positive_number(v):
    try:
        return float(v) > 0
    except (TypeError, ValueError):
        return False


def _split_lines(s):
    return [_clean(x) for x in (s or "").replace(",", "\n").split("\n") if _clean(x)]


def generate_listing(d):
    original_name = _clean(d.get("product_name"))
    name = _tidy_title(original_name)
    if not name:
        raise ValueError("Product name is required.")
    brand = _clean(d.get("brand"))
    model = _clean(d.get("model"))
    storage = _clean(d.get("storage"))
    color = _clean(d.get("color"))
    cond_key = _clean(d.get("condition")) or "unknown"
    cond_label = dict(CONDITIONS).get(cond_key, "Condition not confirmed")
    features = _split_lines(d.get("features"))[:5]
    included = _split_lines(d.get("included"))[:8]

    # ---- eBay-required listing details -----------------------------------
    category = _clean(d.get("category"))
    price = _clean(d.get("price")).lstrip("$").strip()
    price_currency = _clean(d.get("price_currency")).upper()
    price_display = (price + " " + price_currency).strip() if price else ""
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
    if not CONDITION_WORDS.search(" ".join(parts)):
        add(CONDITION_PHRASES.get(cond_key, ""))

    # Parts after brand + product name are our additions; dropping those
    # isn't "trimming" the seller's own words.
    own_parts = 2 if brand else 1
    title = " ".join(parts)
    trimmed = False
    while len(title) > TITLE_LIMIT and len(parts) > own_parts:
        parts.pop()  # drop lowest-priority addition first
        title = " ".join(parts)
    if len(title) > TITLE_LIMIT:  # last resort: hard cut, never mid-word
        title = title[:TITLE_LIMIT].rsplit(" ", 1)[0]
        trimmed = True
    title_changed = title != original_name

    # ---- item specifics --------------------------------------------------
    specifics = []
    if brand:
        specifics.append({"name": "Brand", "value": brand})
    if model:
        specifics.append({"name": "Model", "value": model})
    if color:
        specifics.append({"name": "Color", "value": color})
    if storage:
        specifics.append({"name": "Storage Capacity", "value": storage})
    if cond_key in dict(CONDITIONS):
        specifics.append({"name": "Condition", "value": cond_label})
    # Preserve verified specifics from the source listing without duplicating
    # fields already represented above or inventing missing product facts.
    known_names = {_aspect_key(s["name"]) for s in specifics}
    for raw in (d.get("existing_specifics") or []):
        if not isinstance(raw, dict):
            continue
        field_name = _clean(raw.get("name"))
        field_value = _clean(raw.get("value"))
        if field_name and field_value and _aspect_key(field_name) not in known_names:
            specifics.append({"name": field_name, "value": field_value})
            known_names.add(_aspect_key(field_name))

    # ---- description draft (only promises what the seller entered) ---------
    # Facts come from the listing's own specifics; [brackets] are for the
    # seller to fill in, never invented claims.
    lines = [title, "", "Condition: %s." % cond_label,
             "[Describe the exact condition: any scratches, dents, wear or faults, "
             "and whether everything works as it should.]"]
    if features:
        lines += ["", "Key features:"] + ["\u2022 " + f for f in features]
    spec_lines = ["\u2022 %s: %s" % (x["name"], x["value"]) for x in specifics
                  if x["name"].lower() != "condition"][:15]
    if spec_lines:
        lines += ["", "Specifications:"] + spec_lines
    lines += ["", "What's included:"]
    if included:
        lines += ["\u2022 " + x for x in included]
    else:
        lines.append("[List everything in the box: the item, cables, chargers, manuals, "
                     "original packaging. If something is not included, say so.]")
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
            ship_currency = _clean(d.get("shipping_currency") or price_currency).upper()
            s += " (%s)" % " ".join(x for x in (ship_cost, ship_currency) if x)
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
    if not price:
        readiness.append({"label": "Price", "status": "missing",
                          "note": "eBay won't let you list without a price."})
    elif not _positive_number(price):
        readiness.append({"label": "Price", "status": "missing",
                          "note": "Price must be a number above zero."})
    else:
        readiness.append({"label": "Price", "status": "ok",
                          "note": "%s × %d" % (price_display, quantity)})
    readiness.append({"label": "Category", "status": "ok" if category else "warn",
                      "note": category if category
                      else "Pick the most specific category on eBay \u2014 wrong category hides your listing and changes fees."})
    readiness.append({"label": "Photos", "status": "warn",
                      "note": "Upload clear, well-lit photos from multiple angles, including flaws and everything included. eBay allows up to 24 photos in many listing flows."})
    missing_specs = [_clean(x) for x in (d.get("missing_specifics") or [])
                     if isinstance(x, str) and _clean(x)]
    if missing_specs:
        readiness.append({"label": "Item specifics", "status": "missing",
                          "note": "Required by this category and still empty: %s." % ", ".join(missing_specs[:6])})
    else:
        readiness.append({"label": "Item specifics", "status": "ok",
                          "note": "%d drafted \u2014 fill every specific eBay suggests; filtered search is where sales happen." % len(specifics)})
    readiness.append({"label": "Description", "status": "warn",
                      "note": "Draft ready below \u2014 replace every [bracketed] line with details about your actual item before posting."})
    readiness.append({"label": "Handling time", "status": "ok" if handling else "warn",
                      "note": handling if handling
                      else "Set it on eBay \u2014 late shipment hurts your seller rating."})
    readiness.append({"label": "Shipping", "status": "ok" if ship_label else "warn",
                      "note": (ship_label + (" ($%s)" % ship_cost if ship_key == "flat" and ship_cost else ""))
                      if ship_label else "Many buyers filter for free shipping; flat rate is fine for heavy items."})
    readiness.append({"label": "Returns", "status": "ok" if returns_label else "warn",
                      "note": returns_label if returns_label
                      else "Choose a return policy that fits your business. Top Rated Plus eligibility depends on additional seller and listing requirements."})

    # ---- tips --------------------------------------------------------------
    tips = []
    if len(title) < 60:
        tips.append(
            "Title uses %d/%d chars \u2014 add 1\u20132 more keywords "
            "(model number, size, bundle) to use the full space." % (len(title), TITLE_LIMIT))
    if trimmed:
        tips.append("Your title was over %d chars, so the last words were cut \u2014 "
                    "check the important keywords are still in it." % TITLE_LIMIT)
    if not title_changed:
        tips.append("Your title already covers the facts we could verify \u2014 no rewrite needed.")
    elif name != original_name:
        tips.append("Removed emoji and separator symbols \u2014 buyers don't search for them "
                    "and they use up title space.")
    if not category:
        tips.append("Search your exact item on eBay, open a sold listing, and copy its category path \u2014 that's the category eBay expects.")
    if ship_key == "free":
        tips.append("With free shipping, bake the postage into your price \u2014 buyers filter for it.")
    if returns_label == "No returns accepted":
        tips.append("\u201cNo returns\u201d lowers buyer confidence \u2014 describe the condition precisely and photograph every flaw.")
    if not features:
        tips.append("Add 2\u20133 selling points to the description (battery health, "
                    "accessories, upgrades) \u2014 specifics sell the click.")

    return {"title": title, "title_chars": len(title), "trimmed": trimmed,
            "title_changed": title_changed,
            "specifics": specifics, "description": description,
            "shipping_returns": shipping_returns, "readiness": readiness,
            "tips": tips}
