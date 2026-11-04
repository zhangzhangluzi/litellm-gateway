# gateway-autopilot

Self-maintaining model pool for the LiteLLM gateway (/opt/litellm). No external AI, no human needed.
Python 3 stdlib + PyYAML (`apt install python3-yaml`). No secrets in this directory: keys are read from
/opt/litellm/.env at run time and only kept in memory; logs/state are redacted.

## Files
- `autopilot.py` – the program (`--once` apply, `--dry-run` plan only; test switches `--config --state --sandbox --simulate SUBSTR=CODE --test-fail-verify`)
- `autopilot.yaml` – settings incl. the `autopilot_families` whitelist
- `gateway-autopilot.service` / `.timer` – daily 00:00 UTC (09:00 JST) + RandomizedDelaySec=900, flock
- `demo.sh` – controlled remove → re-add → rollback demo in a temp dir + sandbox container (127.0.0.1:4901)

## Rules
1. Probe each distinct upstream (model + api_base) directly, `max_tokens=8`, 30 s timeout.
   200 = ok; 429 / timeout / 5xx / other 400 = temporary; 401/402/403/404 and 400 mentioning an invalid/unknown/deprecated model = permanent.
   Temporary results reset both streaks (conservative). Runs < `count_interval_hours` (12) apart don't advance streaks.
2. Remove after 2 consecutive permanent runs: all deployments of that upstream (alias + pool members) and the alias in every fallback list
   are removed; full non-secret entries + original indices are saved in `state.json` → `removed`. Skipped (warning) if a pool (`free-*`) would become empty.
3. Re-add a removed model after 2 consecutive 200 (no 402) at its original positions.
4. Auto-add (never on first run; first run records a baseline; ≤2 per run, ≤6 total, needs 2 consecutive 200):
   - OpenRouter `:free` ids with pricing.prompt == pricing.completion == "0", context ≥ 32k, text output, **not present in the baseline**.
   - Whitelisted families: a model in the provider's `/models` matching the family regex with version (integer tuple of capture groups) strictly greater than the highest configured member. Newest one per family.
   New entries copy api_base + key variable from the reference entry, name `<model>-<provider>`, appended to the same pools and to the end of those pools' fallbacks (preferred order unchanged), fallback chain copied from the reference alias; marked `auto_added` in state.
5. Apply: validate in a throw-away container (same image, 127.0.0.1:4900, background health checks off) → backup (keep 10) → write in place → `docker compose -f docker-compose.tunnel.yml up -d --force-recreate litellm` → verify liveliness 200 + /v1/models count + free-auto chat → rollback + re-verify on failure → "critical" if rollback fails.
6. Output: `report-latest.md`, `status.json` (every run), `autopilot.log` (rotating 1 MB × 5); optional `NOTIFY_WEBHOOK_URL` in .env → POST `{"text": ...}`.

## autopilot_families example
```yaml
autopilot_families:
  gemini-flash: {provider: gemini, regex: '^gemini-(\d+(?:\.\d+)?)-flash$'}        # 3.5 < 4 < 4.5
  gpt-oss-groq: {provider: groq, regex: '^openai/gpt-oss-(?:(\d+(?:\.\d+)?)-)?120b$', default_version: 1}
  glm-flash:    {provider: zai,  regex: '^glm-(\d+(?:\.\d+)?)-flash$'}
```
`provider` = litellm prefix (groq/openrouter/gemini) or, for `openai/` + api_base, the host's 2nd-level label (api.cerebras.ai → cerebras; aliases in `provider_aliases`, e.g. z → zai).

## Install
```bash
apt install -y python3-yaml
mkdir -p /opt/litellm/autopilot && cp autopilot.py autopilot.yaml /opt/litellm/autopilot/ && chmod 700 /opt/litellm/autopilot
cd /opt/litellm/autopilot && python3 autopilot.py --dry-run && python3 autopilot.py --once   # first run = baseline only
cp gateway-autopilot.service gateway-autopilot.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now gateway-autopilot.timer && systemctl list-timers gateway-autopilot.timer
```
Status: `cat /opt/litellm/autopilot/report-latest.md`, `cat /opt/litellm/autopilot/status.json`, `tail -50 /opt/litellm/autopilot/autopilot.log`, `journalctl -u gateway-autopilot`.
Note: after an automatic change the YAML is rewritten by PyYAML (comments are dropped; backups keep the originals).
