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
    "shipping": "Many buyers filter for free shipping, and total cost (item + postage) drives the click.",
    "returns": "30-day returns are required for Top Rated Plus and build buyer confidence — 'no returns' puts some buyers off.",
    "description": "Thin descriptions mean more questions, more returns, worse rank. Depth sells.",
    "seller": "Buyers compare feedback before buying — a lower positive % pushes them to a competitor.",
    "condition": "Tons of buyers filter by condition before they ever see your listing.",
    "handling": "Fast dispatch (1 business day) is required for Top Rated Plus and sets buyer expectations.",
    "identifiers": "UPC/EAN/MPN let eBay match your item to its catalog product, which feeds filters and external shopping search.",
}


def _strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", text).strip()


def _check(checks, cid, label, status, detail, fix, weight):
    checks.append({"id": cid, "label": label, "status": status,
                   "detail": detail, "fix": fix, "weight": weight,
                   "why": WHY.get(cid, "")})


GTIN_KEYS = {"upc", "ean", "isbn", "gtin"}
MPN_KEYS = {"mpn", "manufacturer part number"}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _real(v):
    return _norm(v) not in ("", "does not apply", "n a", "na", "unknown")


def analyze(item, similar_prices=None, category_aspects=None):
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
           " ".join(t_fixes) or "Title looks solid.", 18)

    # ---- 2. photos -------------------------------------------------------
    # Real Browse API shape: image.imageUrl is a STRING (main photo) and
    # additionalImages[] holds the rest. (Mock data uses a list — handle both.)
    imgs = []
    main = (item.get("image") or {}).get("imageUrl")
    if isinstance(main, str) and main:
        imgs.append(main)
    elif isinstance(main, list):
        imgs.extend([u for u in main if u])
    for a in item.get("additionalImages") or []:
        u = (a or {}).get("imageUrl")
        if u:
            imgs.append(u)
    n_img = len(imgs)
    if n_img == 0:
        s, f = "fail", "No photos at all — listings without photos barely sell. Add up to 12."
    elif n_img < 4:
        s, f = "warn", f"Only {n_img} photo(s). Top sellers use 8–12 (angles, flaws, scale, packaging)."
    elif n_img < 8:
        s, f = "warn", f"{n_img} photos is okay, but 8–12 converts better."
    else:
        s, f = "pass", f"{n_img} photos — good coverage."
    size_note = ""
    main_img = item.get("image") or {}
    try:
        longest = max(int(main_img.get("width") or 0), int(main_img.get("height") or 0))
    except (TypeError, ValueError, AttributeError):
        longest = 0
    if longest:
        size_note = f", main photo {longest}px"
        if longest < 500:
            s, f = "fail", (f"Main photo is only {longest}px on its longest side — eBay's "
                            "minimum is 500px; 1600px+ is recommended (it enables zoom).")
        elif longest < 1600 and s == "pass":
            s, f = "warn", (f"{n_img} photos, but the main one is {longest}px on its longest "
                            "side. Upload 1600px+ so buyers can zoom.")
    _check(checks, "photos", "Photos", s, f"{n_img}/12 photos{size_note}", f, 14)

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
    sp_detail = f"{n_asp} specifics: " + ", ".join(a.get("name", "") for a in aspects[:6])
    if category_aspects and n_asp:
        have = {_norm(a.get("name")) for a in aspects}
        req_missing = [n for n in category_aspects.get("required", []) if _norm(n) not in have]
        rec_all = category_aspects.get("recommended", [])
        rec_missing = [n for n in rec_all if _norm(n) not in have]
        rec_filled = len(rec_all) - len(rec_missing)
        ratio = rec_filled / len(rec_all) if rec_all else 1.0
        sp_detail += f" — category check: {rec_filled}/{len(rec_all)} recommended filled"
        if req_missing:
            s, f = "fail", ("Missing REQUIRED specifics for this category: "
                            + ", ".join(req_missing[:5]) + ".")
        elif rec_missing and ratio < 0.7:
            s, f = "warn", (f"Only {rec_filled}/{len(rec_all)} recommended specifics filled. Add: "
                            + ", ".join(rec_missing[:5]) + ".")
        else:
            s, f = "pass", f"All required and {rec_filled}/{len(rec_all)} recommended specifics filled."
    _check(checks, "specifics", "Item specifics", s, sp_detail, f, 14)

    # ---- 4. price positioning ----------------------------------------------
    price = None
    try:
        price = float((item.get("price") or {}).get("value"))
    except (TypeError, ValueError):
        pass
    prices = [p for p in (similar_prices or []) if p and p > 0]
    price_weight = 14
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
        s, f, d = "warn", "Not enough comparable listings to judge price (not counted in score).", f"Price ${price:.2f}"
        price_weight = 0
    else:
        s, f, d = "fail", "Could not read the price.", "Price missing"
    _check(checks, "price", "Price positioning", s, d, f, price_weight)

    # ---- 5. shipping --------------------------------------------------------
    ships = item.get("shippingOptions") or []
    free, cost_types = False, set()
    for sh in ships:
        cost = (sh.get("shippingCost") or {}).get("value")
        try:
            if float(cost) == 0:
                free = True
        except (TypeError, ValueError):
            pass
        ct = (sh.get("shippingCostType") or "").replace("_", " ").title()
        if ct:
            cost_types.add(ct)
    loc = item.get("itemLocation") or {}
    loc_str = ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)
    ship_detail = f"{len(ships)} option(s)"
    if cost_types:
        ship_detail += " (" + ", ".join(sorted(cost_types)) + ")"
    if free:
        ship_detail += " — free shipping"
    if loc_str:
        ship_detail += f" — ships from {loc_str}"
    if not ships:
        s, f = "warn", "No shipping options visible via API — confirm free/fast shipping is set on eBay."
    elif free:
        s, f = "pass", "Free shipping offered — a top conversion driver on eBay."
    else:
        s, f = "warn", ("No free shipping. Many buyers filter for it — consider free shipping "
                        "with the cost built into your price.")
    _check(checks, "shipping", "Shipping", s, ship_detail, f, 10)

    # ---- 6. returns ---------------------------------------------------------
    rt = item.get("returnTerms") or {}
    accepted = rt.get("returnsAccepted")
    period = rt.get("returnPeriod") or {}
    try:
        pval = int(str(period.get("value", "")).strip())
    except (TypeError, ValueError):
        pval = None
    punit = str(period.get("unit") or "").upper()
    if accepted is True and pval == 30 and ("DAY" in punit.upper() or not punit):
        s, f = "pass", "30-day returns — good for buyer confidence and Top Rated Plus."
        r_detail = "30-day returns accepted"
    elif accepted is True and pval:
        s, f = "warn", (f"Only {pval}-day returns. 30-day returns are required for "
                        "Top Rated Plus and build buyer confidence.")
        r_detail = f"{pval}-day returns accepted"
    elif accepted is False:
        s, f = "warn", ("No returns accepted. Describe the condition precisely and photograph "
                        "every flaw — buyer confidence drops without a return option.")
        r_detail = "No returns accepted"
    elif accepted is True:
        s, f = "warn", "Returns accepted, but the return window isn't visible — confirm it's 30 days."
        r_detail = "Returns accepted (window unclear)"
    else:
        s, f = "warn", "Couldn't read the returns policy via API — confirm it's set on eBay (30-day recommended)."
        r_detail = "Returns policy not visible"
    _check(checks, "returns", "Returns policy", s, r_detail, f, 5)

    # ---- handling time ---------------------------------------------------------
    try:
        ht = int(item.get("handlingTimeDays"))
    except (TypeError, ValueError):
        ht = None
    if ht is None:
        _check(checks, "handling", "Handling time", "info",
               "Not exposed by eBay's public API",
               "Can't be read automatically — confirm in Seller Hub that handling time is "
               "1 business day.", 0)
    elif ht <= 1:
        _check(checks, "handling", "Handling time", "pass",
               f"{ht} business day(s)", "Ships within 1 business day — meets Top Rated Plus.", 5)
    elif ht <= 3:
        _check(checks, "handling", "Handling time", "warn", f"{ht} business days",
               f"{ht}-day handling. Cut it to 1 business day if you can — it is part of "
               "Top Rated Plus.", 5)
    else:
        _check(checks, "handling", "Handling time", "fail", f"{ht} business days",
               f"{ht}-day handling is slow; buyers expect dispatch within 1–2 days.", 5)

    # ---- 7. description -------------------------------------------------------
    desc_words = len(_strip_html(item.get("description") or item.get("shortDescription") or "").split())
    if desc_words < 30:
        s, f = "fail", "Description is nearly empty — buyers bounce, search suffers. Write 150+ words."
    elif desc_words < 150:
        s, f = "warn", (f"Only ~{desc_words} words. Expand to 150+: condition details, "
                        "what's included, shipping & returns.")
    else:
        s, f = "pass", f"~{desc_words} words — solid."
    _check(checks, "description", "Description", s, f"~{desc_words} words", f, 10)

    # ---- identifiers (UPC / EAN / MPN) -----------------------------------------
    id_vals = {_norm(a.get("name")): a.get("value") for a in aspects}
    has_gtin = _real(item.get("gtin")) or any(_real(v) for k, v in id_vals.items() if k in GTIN_KEYS)
    has_mpn = _real(item.get("mpn")) or any(_real(v) for k, v in id_vals.items() if k in MPN_KEYS)
    if has_gtin:
        s, f, d = "pass", "UPC/EAN/ISBN present — helps eBay match your item to its catalog.", "GTIN present"
    elif has_mpn:
        s, f, d = "pass", "MPN set. Add the UPC/EAN as well if the product has one.", "MPN present, no GTIN"
    else:
        s, f, d = "warn", ("No UPC/EAN/MPN found. Add them if the product has one "
                           "(handmade and vintage items can skip this)."), "No product identifiers"
    _check(checks, "identifiers", "Product identifiers", s, d, f, 4)

    # ---- 8. seller trust --------------------------------------------------------
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
           f"{seller.get('username', '?')} — {fb_pct}% ({fb_score})", f, 4)

    # ---- 9. condition -------------------------------------------------------------
    cond = (item.get("condition") or "").strip()
    if cond:
        s, f = "pass", f"Condition set: {cond}."
    else:
        s, f = "warn", "Condition field empty — buyers filter by condition."
    _check(checks, "condition", "Condition", s, f"Condition: {cond or 'not set'}", f, 2)

    # ---- score ---------------------------------------------------------------------
    total_w = sum(c["weight"] for c in checks)
    earned = sum(c["weight"] for c in checks if c["status"] == "pass") + \
             sum(c["weight"] * 0.5 for c in checks if c["status"] == "warn")
    score = round(earned / total_w * 100) if total_w else 0

    impact = {"fail": 0, "warn": 1, "pass": 2, "info": 3}
    fixes = [{"priority": i + 1, "label": c["label"], "fix": c["fix"],
              "why": WHY.get(c["id"], "")}
             for i, c in enumerate(sorted(
                 [c for c in checks if c["status"] not in ("pass", "info")],
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
