"""Rule-based eBay listing generator (v1).

generate_listing(details) -> {
    title, title_chars, trimmed,
    specifics[{name, value}],
    description, tips[]
}

No LLM, no API calls — pure eBay title/description formulas.
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

    # ---- description draft ------------------------------------------------
    lines = [title, "", "Condition: %s." % cond_label]
    if features:
        lines += ["", "Key features:"] + ["\u2022 " + f for f in features]
    if included:
        lines += ["", "What's included:"] + ["\u2022 " + x for x in included]
    lines += ["",
              "Shipping: ships within 1 business day with tracking.",
              "Returns: 30-day returns accepted."]
    description = "\n".join(lines)

    # ---- tips --------------------------------------------------------------
    tips = []
    if len(title) < 60:
        tips.append(
            "Title uses %d/%d chars \u2014 add 1\u20132 more keywords "
            "(model number, size, bundle) to use the full space." % (len(title), TITLE_LIMIT))
    if trimmed:
        tips.append("Title was trimmed to fit %d chars \u2014 dropped words were "
                    "lowest priority; the important keywords survived." % TITLE_LIMIT)
    tips.append("Add 8\u201312 clear photos: front, back, angles, any flaws, and what's included.")
    tips.append("Fill every item specific eBay suggests \u2014 filtered search is where the sales happen.")
    if not features:
        tips.append("Add 2\u20133 key features above and regenerate \u2014 specifics sell the click.")

    return {"title": title, "title_chars": len(title), "trimmed": trimmed,
            "specifics": specifics, "description": description, "tips": tips}
