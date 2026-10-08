"""Rule-based eBay listing audit engine (v1).

analyze(item, similar_prices) -> report dict:
  {score, checks[{id,label,status,detail,fix,weight}], fixes[{priority,label,fix}], summary}

LLM hook: Phase 2 can call an LLM with this report to write the narrative summary.
"""
import re
from statistics import median

TITLE_LIMIT = 80

# One-line "why this matters" per check — shown in the UI under each fix.
WHY = {
    "title": "eBay matches buyer searches against your title first — a weak title means an invisible listing.",
    "photos": "Buyers can't touch your product, so photos ARE the product. More angles = more trust = more sales.",
    "specifics": "Most buyers filter by brand, size, color… empty specifics means you never appear in filtered search.",
    "price": "Buyers compare prices in one click — 20%+ off the median and you're either ignored or leaving cash behind.",
    "shipping": "Free shipping is one of eBay's strongest ranking signals, and buyers filter for it.",
    "description": "Thin descriptions mean more questions, more returns, worse rank. Depth sells.",
    "seller": "Below 99% positive, buyers will pick a pricier competitor just to feel safe.",
    "condition": "Tons of buyers filter by condition before they ever see your listing.",
}


def _strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _check(checks, cid, label, status, detail, fix, weight):
    checks.append({"id": cid, "label": label, "status": status,
                   "detail": detail, "fix": fix, "weight": weight})


def analyze(item, similar_prices=None):
    checks = []
    title = (item.get("title") or "").strip()

    # ---- 1. title -------------------------------------------------------
    words = title.split()
    caps_words = [w for w in words if len(w) > 2 and w.isupper()]
    seen, dupes = set(), 0
    for w in [x.lower() for x in words]:
        if w in seen:
            dupes += 1
        seen.add(w)
    t_issues, t_fixes = [], []
    if len(title) > TITLE_LIMIT:
        t_issues.append(f"over the {TITLE_LIMIT}-char limit")
    elif len(title) < int(TITLE_LIMIT * 0.6):
        t_fixes.append(f"Only {len(title)}/{TITLE_LIMIT} chars used — "
                       "add keywords buyers search (model, size, color).")
    if caps_words:
        t_fixes.append(f"ALL-CAPS words hurt readability: {', '.join(caps_words[:3])}.")
    if dupes:
        t_fixes.append(f"{dupes} repeated word(s) waste title space.")
    if len(words) < 6:
        t_fixes.append("Very short title — eBay's search needs descriptive keywords.")
    status = "fail" if len(title) > TITLE_LIMIT else ("warn" if t_fixes else "pass")
    detail = f"{len(title)}/{TITLE_LIMIT} chars, {len(words)} words" + \
             (f" — {'; '.join(t_issues)}" if t_issues else "")
    _check(checks, "title", "Title keywords", status, detail,
           " ".join(t_fixes) or "Title looks solid.", 20)

    # ---- 2. photos -------------------------------------------------------
    imgs = ((item.get("image") or {}).get("imageUrl")) or []
    n_img = len(imgs)
    if n_img == 0:
        s, f = "fail", "No photos at all — listings without photos barely sell. Add up to 12."
    elif n_img < 4:
        s, f = "warn", f"Only {n_img} photo(s). Top sellers use 8–12 (angles, flaws, scale, packaging)."
    elif n_img < 8:
        s, f = "warn", f"{n_img} photos is okay, but 8–12 converts better."
    else:
        s, f = "pass", f"{n_img} photos — good coverage."
    _check(checks, "photos", "Photos", s, f"{n_img}/12 photos", f, 15)

    # ---- 3. item specifics -------------------------------------------------
    aspects = item.get("localizedAspects") or []
    n_asp = len(aspects)
    if n_asp == 0:
        s, f = "fail", "No item specifics filled — you are invisible in filtered search."
    elif n_asp < 5:
        s, f = "warn", (f"Only {n_asp} specifics. Fill every applicable one "
                        "(brand, model, size, color…) — filters drive sales.")
    else:
        s, f = "pass", f"{n_asp} specifics filled."
    _check(checks, "specifics", "Item specifics", s,
           f"{n_asp} specifics: " + ", ".join(a.get("name", "") for a in aspects[:6]),
           f, 15)

    # ---- 4. price positioning ----------------------------------------------
    price = None
    try:
        price = float((item.get("price") or {}).get("value"))
    except (TypeError, ValueError):
        pass
    prices = [p for p in (similar_prices or []) if p and p > 0]
    if price and len(prices) >= 5:
        med = median(prices)
        diff = (price - med) / med * 100
        if diff > 20:
            s = "warn"
            f = (f"${price:.2f} is {diff:.0f}% above the market median "
                 f"(${med:.2f}, {len(prices)} similar). Justify it in the description "
                 "or consider matching the market.")
        elif diff < -20:
            s = "warn"
            f = (f"${price:.2f} is {abs(diff):.0f}% below the market median "
                 f"(${med:.2f}). You may be leaving money on the table.")
        else:
            s, f = "pass", f"${price:.2f} sits within ±20% of the market median (${med:.2f})."
        d = f"Your price ${price:.2f} vs median ${med:.2f} ({len(prices)} comparables)"
    elif price:
        s, f, d = "warn", "Not enough comparable listings to judge price.", f"Price ${price:.2f}"
    else:
        s, f, d = "fail", "Could not read the price.", "Price missing"
    _check(checks, "price", "Price positioning", s, d, f, 15)

    # ---- 5. shipping --------------------------------------------------------
    ships = item.get("shippingOptions") or []
    free, handling = False, None
    for sh in ships:
        cost = (sh.get("shippingCost") or {}).get("value")
        try:
            if float(cost) == 0:
                free = True
        except (TypeError, ValueError):
            pass
    if not ships:
        s, f = "warn", "No shipping options visible via API — confirm free/fast shipping is set."
    elif free:
        s, f = "pass", "Free shipping offered — a top conversion driver on eBay."
    else:
        s, f = "warn", "No free shipping. Listings with free shipping win the buy box more often."
    _check(checks, "shipping", "Shipping", s,
           f"{len(ships)} option(s)" + (" — free shipping" if free else ""), f, 10)

    # ---- 6. description -------------------------------------------------------
    desc_words = len(_strip_html(item.get("description") or item.get("shortDescription") or "").split())
    if desc_words < 30:
        s, f = "fail", "Description is nearly empty — buyers bounce, search suffers. Write 150+ words."
    elif desc_words < 150:
        s, f = "warn", (f"Only ~{desc_words} words. Expand to 150+: condition details, "
                        "what's included, shipping & returns.")
    else:
        s, f = "pass", f"~{desc_words} words — solid."
    _check(checks, "description", "Description", s, f"~{desc_words} words", f, 10)

    # ---- 7. seller trust -------------------------------------------------------
    seller = item.get("seller") or {}
    try:
        fb_pct = float(seller.get("feedbackPercentage", 0))
    except (TypeError, ValueError):
        fb_pct = 0
    fb_score = seller.get("feedbackScore", 0)
    if fb_pct >= 99:
        s, f = "pass", f"{fb_pct}% positive ({fb_score} feedback) — trusted seller."
    elif fb_pct >= 97:
        s, f = "warn", f"{fb_pct}% positive — below the 99% buyers expect. Fix issues fast."
    elif fb_pct > 0:
        s, f = "fail", f"{fb_pct}% positive is hurting every listing. Priority: resolve cases, request revisions."
    else:
        s, f = "warn", "No feedback data via API."
    _check(checks, "seller", "Seller trust", s,
           f"{seller.get('username', '?')} — {fb_pct}% ({fb_score})", f, 10)

    # ---- 8. condition ------------------------------------------------------------
    cond = (item.get("condition") or "").strip()
    if cond:
        s, f = "pass", f"Condition set: {cond}."
    else:
        s, f = "warn", "Condition field empty — buyers filter by condition."
    _check(checks, "condition", "Condition", s, f"Condition: {cond or 'not set'}", f, 5)

    # ---- score ---------------------------------------------------------------------
    total_w = sum(c["weight"] for c in checks)
    earned = sum(c["weight"] for c in checks if c["status"] == "pass") + \
             sum(c["weight"] * 0.5 for c in checks if c["status"] == "warn")
    score = round(earned / total_w * 100) if total_w else 0

    impact = {"fail": 0, "warn": 1, "pass": 2}
    fixes = [{"priority": i + 1, "label": c["label"], "fix": c["fix"],
              "why": WHY.get(c["id"], "")}
             for i, c in enumerate(sorted(
                 [c for c in checks if c["status"] != "pass"],
                 key=lambda c: (impact[c["status"]], -c["weight"])))]

    if score >= 80:
        summary = "Strong listing — a few tweaks and it is fully optimized."
    elif score >= 60:
        summary = "Decent foundation, but fixable gaps are costing you visibility and sales."
    elif score >= 40:
        summary = "Several major gaps — work through the fixes in order."
    else:
        summary = "This listing needs serious work — start with the fails at the top."

    return {"score": score, "summary": summary, "checks": checks, "fixes": fixes,
            "title": title,
            "item_url": item.get("itemWebUrl", "")}
