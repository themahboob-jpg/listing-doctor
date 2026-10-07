# Listing Doctor — SCOPE

## Product
**Listing Doctor** — paste any marketplace product URL, get an AI-style audit report card (score 0–100) with prioritized fixes: title keywords, thumbnail/photo count, pricing vs competitors, item specifics completeness, description quality, shipping, seller trust signals.

Vision: multi-platform (eBay → Etsy → TikTok Shop → Walmart → Amazon).
**Phase 1 (now): eBay only** — via eBay's free official Browse API (no scraping, no ToS war).

## Why eBay first
- Free official Browse API (OAuth client-credentials, server-side).
- User understands eBay buying/selling.
- `getItem` returns everything the audit needs: title, price, condition, images, description, item specifics (`localizedAspects`), category path, seller feedback, shipping options.

## Phase 1 features (this build)
1. **Input:** eBay item URL or numeric item ID → parse legacy ID → `getItemByLegacyId` → `getItem`.
2. **Price positioning:** Browse `search` with title keywords → median competitor price → flag ±20%.
3. **Audit engine (rule-based v1):** 8 checks, weighted score, prioritized fixes.
   - Title (80-char limit): utilization %, ALL-CAPS words, word count, repeated words
   - Photos: count of 12 max
   - Item specifics: aspects count (eBay rewards complete specifics for filters)
   - Price vs market median
   - Shipping: free? handling time
   - Description: word count (HTML stripped)
   - Seller: feedback % and score
   - Condition field set?
4. **Report UI:** score dial, pass/warn/fail per check, fixes ordered by impact.
5. **Mock mode:** works end-to-end with realistic sample data when no API key is set (demo + UI verification).

## Free vs Paid (Phase 1b, not built yet)
- Free: 3 audits/month (needs accounts → auth).
- Paid $15/mo: unlimited audits + bulk URL audit + PDF export + change tracking ("your score went 62 → 78").
- Payments: merchant-of-record (Paddle/Lemon Squeezy) — Pakistan-compatible, to verify.

## Tech
- Python Flask backend, vanilla JS + CSS frontend, SQLite later (no DB in Phase 1).
- `ebay_client.py`: OAuth client-credentials token (cached ~2h), Browse API calls, mock fallback.
- `analyzer.py`: pure function `analyze(item, similar)` → JSON report. LLM hook point for narrative (Phase 2).
- Secrets via environment (`.env`), never in code.

## Phases
- **Phase 1 (this build):** eBay audit, working app, mock mode. ⬅ YOU ARE HERE
- **Phase 1b:** accounts, 3-free/mo limit, deploy (Render/Railway), real eBay key.
- **Phase 2:** Etsy (official API) + LLM narrative + thumbnail vision check.
- **Phase 3:** TikTok Shop, Walmart, Amazon connectors as feasible.

## The ONE user action needed
Create a free app at https://developer.ebay.com → "Create app" → copy **Client ID** and **Client Secret** → put in `.env`. Without it the app runs in mock/demo mode.
