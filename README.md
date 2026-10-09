# Listing Doctor — Phase 1 (eBay)

## Run
```bash
cd ~/workspace/listing-doctor
python3 -m venv venv && venv/bin/pip install -q flask requests   # one time
venv/bin/python app.py                                            # -> http://localhost:5000
```

## Modes
- **Mock/demo** (default): no keys needed. Audits run on built-in sample data.
- **Live**: create a free app at https://developer.ebay.com, copy `.env.example`
  to `.env`, fill in `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`, restart.

## Files
| File | What |
|---|---|
| `app.py` | Flask app: `/` UI, `/api/audit` JSON, `/api/health` |
| `ebay_client.py` | OAuth client-credentials + Browse API (`getItemByLegacyId`, `getItem`, `search`) + mock fallback |
| `analyzer.py` | Rule-based audit engine → score 0–100, 11 checks, prioritized fixes |
| `templates/index.html` | Single-page UI |
| `static/style.css` | Dark theme |
| `SCOPE.md` | Full product scope, phases, free vs paid |

## Roadmap
Phase 1b: accounts + 3 free audits/mo + deploy. Phase 2: Etsy API, LLM narrative,
thumbnail vision check. Phase 3: TikTok Shop / Walmart / Amazon.
