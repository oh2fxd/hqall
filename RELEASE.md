# Release checklist for hqall

This is the practical release checklist for a GitHub-hosted Python app.

## Before publishing

- [ ] Confirm the app starts with the documented local command.
- [ ] Run the full test suite: `PYTHONPATH=. python3 -m pytest -q`
- [ ] Review README installation and environment instructions.
- [ ] Check that optional environment variables are documented and safe to omit.
- [ ] Confirm the app still serves the frontend and API on the expected port.
- [ ] Confirm no secrets are committed to the repository.

## Recommended release steps

1. Install the dependencies directly:
   ```bash
   pip install -r requirements.txt
   ```
2. Run the app locally:
   ```bash
   python3 -m uvicorn app:app --host 0.0.0.0 --port 8077
   ```
3. Open the app and sanity-check the map loads and the API responds.
4. Verify `/api/health` returns a successful status.
5. Tag the release and push it to GitHub.

## Optional environment variables

These are optional and the app keeps working without them:

- `HQALL_REPO` — enables GitHub update checks
- `AIS_API_KEY` — enables the vessel layer
- `APRS_CALLSIGN` / `APRS_PASSCODE` — APRS-IS login info
- `APRS_API_KEY` — aprs.fi fallback
- `HQALL_CONTACT` — User-Agent contact string

## Final sanity check

Before tagging a release:

```bash
PYTHONPATH=. python3 -m pytest -q
python3 -m uvicorn app:app --host 127.0.0.1 --port 8077
```

Then open <http://127.0.0.1:8077/> and verify the map loads without runtime errors.
