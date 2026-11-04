#!/usr/bin/env python3
"""gateway-autopilot: self-maintaining model pool for the LiteLLM gateway.

Daily: probe every upstream directly, auto-remove models with 2 consecutive
permanent failures, auto-re-add removed models after 2 consecutive 200s, and
conservatively auto-add machine-verifiable free / newer-family models.
All config changes: backup -> validate in temp container -> write -> recreate
-> verify -> rollback on failure.  Secrets are only kept in memory.
"""
import argparse, copy, datetime as dt, fcntl, hashlib, json, logging, os, re
import shutil, subprocess, sys, tempfile, time, urllib.request, urllib.error
from logging.handlers import RotatingFileHandler
import yaml

VERSION = "1.0"
UA = "gateway-autopilot/" + VERSION
DEFAULTS = {
    "litellm_dir": "/opt/litellm",
    "config": "/opt/litellm/litellm-config.yaml",
    "env_file": "/opt/litellm/.env",
    "compose_file": "docker-compose.tunnel.yml",
    "service": "litellm",
    "image": "ghcr.io/berriai/litellm:v1.106.6",
    "live_url": "http://127.0.0.1:4000",
    "validate_port": 4900,
    "sandbox_port": 4901,
    "pool_prefixes": ["free-"],
    "verify_pool": "free-auto",
    "master_key_env": "LITELLM_MASTER_KEY",
    "derived_env": {"CLOUDFLARE_API_BASE":
        "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1"},
    "provider_bases": {
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    "provider_key_env": {"groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY",
                         "gemini": "GEMINI_API_KEY"},
    "provider_aliases": {"z": "zai", "cohere": "cohere"},
    "probe_timeout": 30,
    "remove_after": 2, "readd_after": 2, "add_after": 2,
    "max_add_per_run": 2, "max_auto_added_total": 6, "max_candidate_probes": 6,
    "count_interval_hours": 12,
    "openrouter_free": {"enabled": True, "min_context": 32000},
    "autopilot_families": {},
    "backups_keep": 10,
}

# ---------------------------------------------------------------- helpers
SECRETS = set()

def redact(s):
    s = str(s)
    for v in sorted(SECRETS, key=len, reverse=True):
        s = s.replace(v, "***")
    s = re.sub(r"(sk-|gsk_|csk-|nvapi-|AIza)[A-Za-z0-9_\-]{8,}", r"\1***", s)
    return s

class RedactFilter(logging.Filter):
    def filter(self, rec):
        rec.msg = redact(rec.getMessage()); rec.args = None; return True

log = logging.getLogger("autopilot")

def setup_logging(path):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
    fmt.converter = time.gmtime
    for h in (RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5),
              logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt); h.addFilter(RedactFilter()); log.addHandler(h)

def load_env(path):
    env = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        env[k.strip()] = v
        if len(v) >= 8: SECRETS.add(v)
    return env

def now(): return dt.datetime.now(dt.timezone.utc)
def iso(t=None): return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")

def http(method, url, headers=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": UA, "Content-Type": "application/json"}; h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(20_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try: txt = e.read(4000).decode("utf-8", "replace")
        except Exception: txt = ""
        return e.code, txt
    except Exception as e:
        return None, type(e).__name__ + ": " + str(e)[:200]

def sha256(path): return hashlib.sha256(open(path, "rb").read()).hexdigest()

# ---------------------------------------------------------------- config model
def is_pool(name, cfg): return any(name.startswith(p) for p in cfg["pool_prefixes"])

def sig(lp):
    """Upstream identity of a deployment (non-secret)."""
    return lp.get("model", "") + "|" + str(lp.get("api_base", ""))

def provider_of(lp):
    p = _provider_of(lp)
    return PROVIDER_ALIASES.get(p, p)

PROVIDER_ALIASES = {}

def _provider_of(lp):
    m = lp.get("model", "")
    pre = m.split("/", 1)[0]
    if pre != "openai" or "api_base" not in lp: return pre
    base = str(lp["api_base"])
    if base.startswith("os.environ/"):
        return base.split("/", 1)[1].split("_")[0].lower()
    host = urllib.parse.urlparse(base).hostname or ""
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else host

import urllib.parse

def upstream_id(lp):
    m = lp.get("model", "")
    return m.split("/", 1)[1] if "/" in m else m

def resolve(v, env):
    if isinstance(v, str) and v.startswith("os.environ/"): return env.get(v.split("/", 1)[1])
    return v

def endpoint(lp, env, cfg):
    """-> (base_url, api_key, model_id) for a direct upstream call."""
    pre = lp.get("model", "").split("/", 1)[0]
    if "api_base" in lp: base = resolve(lp["api_base"], env)
    else: base = cfg["provider_bases"].get(pre)
    key = resolve(lp.get("api_key"), env)
    if not key and pre in cfg["provider_key_env"]: key = env.get(cfg["provider_key_env"][pre])
    return (base.rstrip("/") if base else None), key, upstream_id(lp)

BAD_MODEL_RE = re.compile(r"model|not.?found|does not exist|deprecat|decommission|invalid|unknown|unsupported", re.I)

def classify(code, body):
    if code == 200: return "ok"
    if code is None or code == 429 or code == 408 or (code and code >= 500): return "temp"
    if code in (401, 402, 403, 404): return "perm"
    if code == 400: return "perm" if BAD_MODEL_RE.search(body or "") else "temp"
    return "temp"

def probe(lp, env, cfg, overrides):
    s = sig(lp)
    for pat, code in overrides.items():
        if pat in s: return code, "simulated(test switch)"
    base, key, mid = endpoint(lp, env, cfg)
    if not base or not key: return None, "no endpoint/key resolvable"
    code, body = http("POST", base + "/chat/completions", {"Authorization": "Bearer " + key},
                      {"model": mid, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
                      timeout=cfg["probe_timeout"])
    return code, redact(body)[:300]

# ---------------------------------------------------------------- mutations
def fallbacks(conf):
    return conf.setdefault("router_settings", {}).setdefault("fallbacks", [])

def pool_counts(conf, cfg):
    c = {}
    for e in conf["model_list"]:
        if is_pool(e["model_name"], cfg): c[e["model_name"]] = c.get(e["model_name"], 0) + 1
    return c

def remove_upstream(conf, s, cfg):
    """Remove all deployments with upstream signature s. Returns record or (None, reason)."""
    ml = conf["model_list"]
    idx = [i for i, e in enumerate(ml) if sig(e["litellm_params"]) == s]
    if not idx: return None, "not present"
    names = [ml[i]["model_name"] for i in idx]
    counts = pool_counts(conf, cfg)
    for n in set(names):
        if is_pool(n, cfg) and counts.get(n, 0) - names.count(n) < 1:
            return None, "pool %s would become empty" % n
    aliases = sorted({n for n in names if not is_pool(n, cfg)
                      and all(sig(e["litellm_params"]) == s for e in ml if e["model_name"] == n)})
    rec = {"signature": s, "aliases": aliases,
           "entries": [{"index": i, "entry": copy.deepcopy(ml[i])} for i in idx],
           "fallback_positions": [], "fallback_keys": []}
    for i in reversed(idx): del ml[i]
    fb = fallbacks(conf)
    for j, item in enumerate(list(fb)):
        (k, lst), = item.items()
        if k in aliases:
            rec["fallback_keys"].append({"index": j, "item": copy.deepcopy(item)}); continue
        for pos, m in enumerate(lst):
            if m in aliases: rec["fallback_positions"].append({"key": k, "index": pos, "model": m})
    conf["router_settings"]["fallbacks"] = [
        {k: [m for m in v if m not in aliases]} for it in fb for k, v in it.items() if k not in aliases]
    return rec, "ok"

def readd(conf, rec):
    ml = conf["model_list"]
    for e in sorted(rec["entries"], key=lambda x: x["index"]):
        ml.insert(min(e["index"], len(ml)), copy.deepcopy(e["entry"]))
    fb = fallbacks(conf)
    for fk in sorted(rec["fallback_keys"], key=lambda x: x["index"]):
        k = list(fk["item"])[0]
        if not any(k in it for it in fb): fb.insert(min(fk["index"], len(fb)), copy.deepcopy(fk["item"]))
    for p in rec["fallback_positions"]:
        for it in fb:
            if p["key"] in it and p["model"] not in it[p["key"]]:
                it[p["key"]].insert(min(p["index"], len(it[p["key"]])), p["model"])

def add_new(conf, cand):
    ml, fb = conf["model_list"], fallbacks(conf)
    ml.append({"model_name": cand["alias"], "litellm_params": copy.deepcopy(cand["litellm_params"])})
    for pool in cand["pools"]:
        ml.append({"model_name": pool, "litellm_params": copy.deepcopy(cand["litellm_params"])})
        for it in fb:
            if pool in it and cand["alias"] not in it[pool]: it[pool].append(cand["alias"])
    if cand.get("ref_alias"):
        for it in fb:
            if cand["ref_alias"] in it:
                chain = [m for m in it[cand["ref_alias"]] if m != cand["alias"]]
                fb.append({cand["alias"]: chain}); break

# ---------------------------------------------------------------- discovery
def list_models(base, key, timeout=30):
    if "generativelanguage.googleapis.com" in base:   # Gemini: native list (OpenAI-compat /models rejects)
        code, body = http("GET", "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
                          {"x-goog-api-key": key}, timeout=timeout)
        if code != 200: return None, code
        try: return [{"id": m["name"]} for m in json.loads(body).get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])], 200
        except Exception: return None, "badjson"
    code, body = http("GET", base + "/models", {"Authorization": "Bearer " + key}, timeout=timeout)
    if code != 200: return None, code
    try: data = json.loads(body).get("data", [])
    except Exception: return None, "badjson"
    return data, 200

def vtuple(m, default):
    g = [x for x in m.groups() if x is not None]
    if not g: return tuple(int(x) for x in str(default).split("."))
    return tuple(int(x) for x in re.findall(r"\d+", ".".join(g)))

def discover(conf, env, cfg, state):
    """Return candidate dicts, strictly rule based."""
    ml = conf["model_list"]
    present = {sig(e["litellm_params"]) for e in ml}
    removed = {r["signature"] for r in state.get("removed", [])}
    rejected = set(state.get("rejected_candidates", []))
    aliases = {e["model_name"]: e["litellm_params"] for e in ml if not is_pool(e["model_name"], cfg)}
    def pools_with(pred):
        return sorted({e["model_name"] for e in ml if is_pool(e["model_name"], cfg) and pred(e["litellm_params"])})
    cands, seen_lists = [], {}
    # Rule A: OpenRouter :free with zero prompt+completion price, new since baseline
    orc = cfg["openrouter_free"]
    or_refs = [(a, lp) for a, lp in aliases.items() if provider_of(lp) == "openrouter"]
    if orc.get("enabled") and or_refs:
        base, key, _ = endpoint(or_refs[0][1], env, cfg)
        data, code = list_models(base, key)
        if data is None: log.warning("openrouter /models failed: %s", code)
        else:
            free = sorted(m["id"] for m in data if m.get("id", "").endswith(":free")
                          and str(m.get("pricing", {}).get("prompt")) == "0"
                          and str(m.get("pricing", {}).get("completion")) == "0"
                          and (m.get("context_length") or 0) >= orc.get("min_context", 0)
                          and "text" in (m.get("architecture", {}).get("output_modalities") or ["text"]))
            seen_lists["openrouter_free"] = free
            baseline = set(state.get("openrouter_free_baseline") or free)
            for mid in free:
                if mid in baseline: continue
                ref_alias, ref = or_refs[0]
                lp = {"model": "openrouter/" + mid, "api_key": ref["api_key"]}
                cands.append({"rule": "openrouter_free", "family": None, "litellm_params": lp,
                              "alias": mid.split("/")[-1].replace(":free", "") + "-openrouter",
                              "ref_alias": ref_alias,
                              "pools": pools_with(lambda p: provider_of(p) == "openrouter")})
    # Rule B: whitelisted families, strictly newer version than configured max
    lists = {}
    for fam, fd in cfg["autopilot_families"].items():
        rx = re.compile(fd["regex"])
        members = [(a, lp, rx.match(upstream_id(lp))) for a, lp in aliases.items()
                   if provider_of(lp) == fd["provider"] and rx.match(upstream_id(lp))]
        if not members: continue
        best = max(members, key=lambda x: vtuple(x[2], fd.get("default_version", 0)))
        bver = vtuple(best[2], fd.get("default_version", 0))
        ref_alias, ref = best[0], best[1]
        base, key, _ = endpoint(ref, env, cfg)
        lk = (base, key)
        if lk not in lists:
            lists[lk] = list_models(base, key)
        data, code = lists[lk]
        if data is None: log.info("family %s: /models unavailable (%s)", fam, code); continue
        newer = []
        for m in data:
            mid = re.sub(r"^models/", "", m.get("id", ""))
            mm = rx.match(mid)
            if mm and vtuple(mm, fd.get("default_version", 0)) > bver: newer.append((vtuple(mm, 0), mid))
        if not newer: continue
        mid = max(newer)[1]
        lp = {k: v for k, v in ref.items() if k in ("api_base", "api_key")}
        lp["model"] = ref["model"].split("/", 1)[0] + "/" + mid
        lp = {"model": lp.pop("model"), **lp}
        rsig = sig(ref)
        cands.append({"rule": "family:" + fam, "family": fam, "litellm_params": lp,
                      "alias": mid.split("/")[-1] + "-" + fd["provider"], "ref_alias": ref_alias,
                      "pools": pools_with(lambda p: sig(p) == rsig)})
    out, names = [], {e["model_name"] for e in ml}
    for c in cands:
        s = sig(c["litellm_params"])
        if s in present or s in removed or s in rejected or c["alias"] in names: continue
        c["signature"] = s; out.append(c)
    return out, seen_lists

# ---------------------------------------------------------------- docker ops
def run(cmd, timeout=600, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, redact((p.stdout + p.stderr)[-1500:])

def write_envfile(env, cfg, d):
    path = os.path.join(d, "env")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        for k, v in env.items(): f.write("%s=%s\n" % (k, v))
        for k, tpl in cfg["derived_env"].items():
            f.write("%s=%s\n" % (k, re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), ""), tpl)))
    return path

def wait_live(url, secs=240):
    t0 = time.time()
    while time.time() - t0 < secs:
        code, _ = http("GET", url + "/health/liveliness", timeout=5)
        if code == 200: return True
        time.sleep(5)
    return False

def count_models(url, mk):
    code, body = http("GET", url + "/v1/models", {"Authorization": "Bearer " + mk}, timeout=20)
    if code != 200: return None
    return len(json.loads(body).get("data", []))

def chat_ok(url, mk, model):
    for _ in range(3):
        code, body = http("POST", url + "/v1/chat/completions", {"Authorization": "Bearer " + mk},
                          {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
                          timeout=90)
        if code == 200: return True, 200
        time.sleep(10)
    return False, code

def start_container(name, cfgpath, port, envfile, cfg):
    run(["docker", "rm", "-f", name], 60)
    return run(["docker", "run", "-d", "--rm", "--name", name, "--memory", "680m",
                "--env-file", envfile, "-e", "TMPDIR=/tmp",
                "-v", cfgpath + ":/app/config.yaml:ro", "-p", "127.0.0.1:%d:4000" % port,
                cfg["image"], "--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "4000"], 300)

def validate(conf, env, cfg, tmpd):
    """Load candidate config in a throw-away container on a loopback temp port."""
    vconf = copy.deepcopy(conf)
    vconf.setdefault("general_settings", {})["background_health_checks"] = False
    p = os.path.join(tmpd, "validate.yaml"); dump(vconf, p); os.chmod(p, 0o644)
    envf = write_envfile(env, cfg, tmpd)
    name = "litellm-autopilot-validate"; port = cfg["validate_port"]
    rc, out = start_container(name, p, port, envf, cfg)
    if rc != 0: return False, "docker run failed: " + out
    try:
        url = "http://127.0.0.1:%d" % port
        if not wait_live(url): 
            _, logs = run(["docker", "logs", "--tail", "30", name], 30)
            return False, "liveliness timeout; logs: " + logs[-600:]
        n = count_models(url, env.get(cfg["master_key_env"], ""))
        exp = len({e["model_name"] for e in conf["model_list"]})
        if n != exp: return False, "models %s != expected %s" % (n, exp)
        return True, "loaded, %d model names" % n
    finally:
        run(["docker", "rm", "-f", name], 60)

def dump(conf, path):
    with open(path, "w") as f:
        f.write("# Managed by gateway-autopilot (comments are not preserved after auto changes).\n")
        yaml.safe_dump(conf, f, sort_keys=False, allow_unicode=True, default_flow_style=None, width=200)

# ---------------------------------------------------------------- apply
class Applier:
    def __init__(self, cfg, env, args, base):
        self.cfg, self.env, self.args, self.base = cfg, env, args, base
        self.sandbox = args.sandbox
        self.url = "http://127.0.0.1:%d" % cfg["sandbox_port"] if self.sandbox else cfg["live_url"]

    def reload(self, tmpd):
        if self.sandbox:
            envf = write_envfile(self.env, self.cfg, tmpd)
            return start_container("litellm-autopilot-sandbox", os.path.abspath(self.cfg["config"]),
                                   self.cfg["sandbox_port"], envf, self.cfg)
        return run(["docker", "compose", "-f", self.cfg["compose_file"], "up", "-d", "--force-recreate",
                    self.cfg["service"]], 600, cwd=self.cfg["litellm_dir"])

    def verify(self, conf, force_fail=False):
        mk = self.env.get(self.cfg["master_key_env"], "")
        if not wait_live(self.url): return False, "liveliness not 200"
        exp = len({e["model_name"] for e in conf["model_list"]})
        time.sleep(3)
        n = count_models(self.url, mk)
        if n != exp: return False, "/v1/models %s != %s" % (n, exp)
        ok, code = chat_ok(self.url, mk, self.cfg["verify_pool"])
        if not ok: return False, "%s chat failed (%s)" % (self.cfg["verify_pool"], code)
        if force_fail: return False, "forced verify failure (test switch --test-fail-verify)"
        return True, "liveliness 200, models %d, %s chat 200" % (n, self.cfg["verify_pool"])

    def apply(self, old_conf, new_conf):
        cfgp = self.cfg["config"]; bdir = os.path.join(self.base, "backups"); os.makedirs(bdir, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="autopilot-") as tmpd:
            os.chmod(tmpd, 0o700)
            if self.sandbox: run(["docker", "rm", "-f", "litellm-autopilot-sandbox"], 60)  # 1 vCPU/2GB: one extra at a time
            ok, msg = validate(new_conf, self.env, self.cfg, tmpd)
            log.info("validate: %s %s", ok, msg)
            if not ok:
                if self.sandbox: self.reload(tmpd)
                return "validate_failed", msg
            bk = os.path.join(bdir, "litellm-config.%s.yaml" % now().strftime("%Y%m%dT%H%M%SZ"))
            shutil.copy2(cfgp, bk)
            for old in sorted(os.listdir(bdir))[:-self.cfg["backups_keep"]]: os.remove(os.path.join(bdir, old))
            st = os.stat(cfgp)
            # in-place write keeps the inode of the single-file bind mount
            tmpn = os.path.join(tmpd, "new.yaml"); dump(new_conf, tmpn)
            with open(tmpn) as s, open(cfgp, "w") as d: d.write(s.read())
            os.chown(cfgp, st.st_uid, st.st_gid); os.chmod(cfgp, st.st_mode & 0o777)
            rc, out = self.reload(tmpd); log.info("reload rc=%s %s", rc, out[-300:])
            ok, vmsg = self.verify(new_conf, force_fail=self.args.test_fail_verify) if rc == 0 else (False, "reload rc=%s" % rc)
            log.info("verify: %s %s", ok, vmsg)
            if ok: return "applied", "backup=%s; %s" % (os.path.basename(bk), vmsg)
            shutil.copyfile(bk, cfgp)
            rc, out = self.reload(tmpd)
            ok2, vmsg2 = self.verify(old_conf) if rc == 0 else (False, "reload rc=%s" % rc)
            if ok2: return "rolled_back", "apply verify failed (%s); rolled back to %s; %s" % (vmsg, os.path.basename(bk), vmsg2)
            return "critical", "apply failed (%s) AND rollback verify failed (%s)" % (vmsg, vmsg2)

# ---------------------------------------------------------------- main logic
def counted(state, cfg):
    last = state.get("last_counted_run")
    if not last: return True
    t = dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return (now() - t).total_seconds() >= cfg["count_interval_hours"] * 3600

def bump(rec, cls, code, body, do_count):
    rec["last"] = {"time": iso(), "class": cls, "code": code, "detail": body[:200]}
    if not do_count: return
    rec["consecutive_perm"] = rec.get("consecutive_perm", 0) + 1 if cls == "perm" else 0
    rec["consecutive_ok"] = rec.get("consecutive_ok", 0) + 1 if cls == "ok" else 0
    if code == 402: rec["seen_402"] = True
    elif cls == "ok" and rec.get("consecutive_ok", 0) == 1: rec["seen_402"] = False

def main():
    ap = argparse.ArgumentParser(description="gateway-autopilot")
    ap.add_argument("--once", action="store_true", help="run one cycle and apply changes")
    ap.add_argument("--dry-run", action="store_true", help="probe and plan only; write nothing")
    ap.add_argument("--settings", default=None, help="autopilot.yaml (default: <base>/autopilot.yaml)")
    ap.add_argument("--base", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--config", help="override litellm config path (testing)")
    ap.add_argument("--state", help="override state.json path (testing)")
    ap.add_argument("--sandbox", action="store_true", help="test mode: reload a temp container on sandbox_port instead of the live service")
    ap.add_argument("--simulate", action="append", default=[], metavar="SUBSTR=CODE",
                    help="test switch: pretend upstream signatures containing SUBSTR return CODE")
    ap.add_argument("--test-fail-verify", action="store_true", help="test switch: force post-apply verify to fail (exercises rollback)")
    args = ap.parse_args()
    if not (args.once or args.dry_run): ap.error("use --once or --dry-run")
    base = args.base; os.makedirs(base, exist_ok=True)
    cfg = copy.deepcopy(DEFAULTS)
    sp = args.settings or os.path.join(base, "autopilot.yaml")
    if os.path.exists(sp): cfg.update(yaml.safe_load(open(sp)) or {})
    PROVIDER_ALIASES.update(cfg["provider_aliases"])
    if args.config: cfg["config"] = os.path.abspath(args.config)
    state_p = args.state or os.path.join(base, "state.json")
    setup_logging(os.path.join(base, "autopilot.log"))
    lockf = open(os.path.join(base, ".lock"), "w")
    try: fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: log.error("another run holds the lock; exit"); return 3
    env = load_env(cfg["env_file"])
    for k, tpl in cfg["derived_env"].items():
        env.setdefault(k, re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), ""), tpl))
    for k in cfg["derived_env"]:
        if len(env.get(k, "")) >= 8: SECRETS.add(env[k])
    overrides = {}
    for s in args.simulate:
        a, b = s.rsplit("=", 1); overrides[a] = int(b)
    state = json.load(open(state_p)) if os.path.exists(state_p) else {}
    first = not state.get("baseline_done")
    do_count = counted(state, cfg)
    mode = "dry-run" if args.dry_run else ("sandbox" if args.sandbox else "live")
    log.info("=== run start mode=%s first=%s counted=%s config=%s sha=%s", mode, first, do_count,
             cfg["config"], sha256(cfg["config"])[:12])
    conf = yaml.safe_load(open(cfg["config"]))
    old_conf = copy.deepcopy(conf)
    models = state.setdefault("models", {})
    report = {"time": iso(), "mode": mode, "first_run": first, "probes": [], "changes": [],
              "warnings": [], "critical": []}

    # 1. probe every configured upstream + removed ones
    targets = {}
    for e in conf["model_list"]:
        s = sig(e["litellm_params"])
        t = targets.setdefault(s, {"lp": e["litellm_params"], "names": []}); t["names"].append(e["model_name"])
    for r in state.get("removed", []):
        targets.setdefault(r["signature"], {"lp": r["entries"][0]["entry"]["litellm_params"], "names": ["(removed) " + ",".join(r["aliases"])]})
    for s, t in targets.items():
        code, body = probe(t["lp"], env, cfg, overrides)
        cls = classify(code, body)
        rec = models.setdefault(s, {}); rec["names"] = sorted(set(t["names"]))
        bump(rec, cls, code, body, do_count)
        report["probes"].append({"signature": s, "names": rec["names"], "code": code, "class": cls,
                                 "consecutive_perm": rec.get("consecutive_perm", 0), "consecutive_ok": rec.get("consecutive_ok", 0)})
        log.info("probe %-60s %s %s perm=%s ok=%s", s, code, cls, rec.get("consecutive_perm"), rec.get("consecutive_ok"))
        if cls != "ok": log.info("   detail: %s", body[:160])

    new_conf = copy.deepcopy(conf)
    removed_list = state.setdefault("removed", [])
    pending_removed, pending_readd, pending_add = [], [], []

    # 2. removal
    for s, rec in models.items():
        if s in targets and any(not n.startswith("(removed)") for n in targets[s]["names"]) \
                and rec.get("consecutive_perm", 0) >= cfg["remove_after"]:
            r, why = remove_upstream(new_conf, s, cfg)
            if r is None:
                report["warnings"].append("not removing %s: %s" % (s, why)); log.warning("not removing %s: %s", s, why); continue
            r.update({"date": iso(), "reason": "%d consecutive permanent failures, last %s" % (rec["consecutive_perm"], rec["last"]["code"]),
                      "last_detail": rec["last"]["detail"]})
            pending_removed.append(r)
            report["changes"].append("REMOVE %s (%s)" % (",".join(r["aliases"]) or s, r["reason"]))
    # 3. re-add
    for r in removed_list:
        rec = models.get(r["signature"], {})
        if rec.get("consecutive_ok", 0) >= cfg["readd_after"] and not rec.get("seen_402"):
            readd(new_conf, r); pending_readd.append(r)
            report["changes"].append("READD %s (%d consecutive 200)" % (",".join(r["aliases"]), rec["consecutive_ok"]))
    # 4. discovery / add (never on first run)
    cands_state = state.setdefault("candidates", {})
    try:
        cands, seen = discover(conf, env, cfg, state)
    except Exception as ex:
        cands, seen = [], {}; report["warnings"].append("discovery error: %s" % type(ex).__name__); log.exception("discovery")
    if first:
        if "openrouter_free" in seen: state["openrouter_free_baseline"] = seen["openrouter_free"]
        report["changes_note"] = "first run: baseline only, no additions"
        log.info("first run: baseline recorded (%d openrouter free ids), %d candidates ignored",
                 len(seen.get("openrouter_free", [])), len(cands))
        for c in cands: log.info("   baseline candidate (not added): %s %s", c["alias"], c["rule"])
    else:
        total_auto = sum(1 for v in cands_state.values() if v.get("auto_added"))
        for c in cands[:cfg["max_candidate_probes"]]:
            cs = cands_state.setdefault(c["signature"], {"first_seen": iso(), "rule": c["rule"], "alias": c["alias"]})
            if cs.get("auto_added"): continue
            code, body = probe(c["litellm_params"], env, cfg, overrides)
            cls = classify(code, body); bump(cs, cls, code, body, do_count)
            log.info("candidate %s (%s): %s ok=%s", c["alias"], c["rule"], code, cs.get("consecutive_ok"))
            if cls == "perm" and do_count and cs.get("consecutive_perm", 0) >= 3:
                state.setdefault("rejected_candidates", []).append(c["signature"])
            if cs.get("consecutive_ok", 0) >= cfg["add_after"] and len(pending_add) < cfg["max_add_per_run"] \
                    and total_auto + len(pending_add) < cfg["max_auto_added_total"]:
                add_new(new_conf, c); pending_add.append(c)
                report["changes"].append("ADD %s -> pools %s (%s)" % (c["alias"], c["pools"], c["rule"]))

    changed = bool(pending_removed or pending_readd or pending_add)
    result = "no_change"
    if changed and not args.dry_run:
        result, msg = Applier(cfg, env, args, base).apply(old_conf, new_conf)
        report["apply"] = {"result": result, "detail": msg}
        if result == "applied":
            removed_list.extend(pending_removed)
            for r in pending_readd: removed_list.remove(r)
            for r in pending_removed: models[r["signature"]]["consecutive_ok"] = 0
            for c in pending_add:
                cands_state[c["signature"]].update({"auto_added": True, "added": iso(), "alias": c["alias"], "pools": c["pools"]})
        elif result == "critical": report["critical"].append(msg)
        else: report["warnings"].append("%s: %s" % (result, msg))
    elif changed:
        result = "dry_run_planned"
    if do_count: state["last_counted_run"] = iso()
    state["baseline_done"] = True
    state["last_run"] = {"time": iso(), "mode": mode, "result": result, "changes": report["changes"]}
    report["result"] = result
    report["config_sha256"] = sha256(cfg["config"])
    if not args.dry_run:
        tmp = state_p + ".tmp"
        with open(tmp, "w") as f: f.write(redact(json.dumps(state, indent=1, ensure_ascii=False)))
        os.replace(tmp, state_p); os.chmod(state_p, 0o600)
        write_reports(base, report, state, env, args)
    else:
        print(redact(json.dumps({k: report[k] for k in ("changes", "warnings")}, ensure_ascii=False, indent=1)))
    log.info("=== run end result=%s changes=%d warnings=%d critical=%d", result, len(report["changes"]),
             len(report["warnings"]), len(report["critical"]))
    return 2 if report["critical"] else 0

def write_reports(base, report, state, env, args):
    pre = "sandbox-" if args.sandbox or args.state else ""
    with open(os.path.join(base, pre + "status.json"), "w") as f:
        f.write(redact(json.dumps(report, indent=1, ensure_ascii=False)))
    if not (report["changes"] or report["critical"] or report["first_run"] or report["warnings"]):
        return
    L = ["# gateway-autopilot report", "", "- time (UTC): %s  mode: %s  result: **%s**" % (report["time"], report["mode"], report["result"]), ""]
    for t, k in (("Changes", "changes"), ("CRITICAL", "critical"), ("Warnings", "warnings")):
        if report[k]: L += ["## " + t] + ["- " + x for x in report[k]] + [""]
    if "apply" in report: L += ["## Apply", "- " + report["apply"]["detail"], ""]
    L += ["## Probes", "| upstream | names | code | class | perm streak | ok streak |", "|---|---|---|---|---|---|"]
    for p in report["probes"]:
        L.append("| %s | %s | %s | %s | %s | %s |" % (p["signature"].replace("|", " @ ").rstrip(" @"), ",".join(p["names"]), p["code"], p["class"], p["consecutive_perm"], p["consecutive_ok"]))
    L += ["", "removed: %s" % [",".join(r["aliases"]) for r in state.get("removed", [])],
          "auto_added: %s" % [v["alias"] for v in state.get("candidates", {}).values() if v.get("auto_added")]]
    with open(os.path.join(base, pre + "report-latest.md"), "w") as f: f.write(redact("\n".join(L)) + "\n")
    url = env.get("NOTIFY_WEBHOOK_URL")
    if url and (report["changes"] or report["critical"]):
        txt = "gateway-autopilot %s: %s" % (report["result"], "; ".join(report["critical"] + report["changes"])[:900])
        code, _ = http("POST", url, body={"text": redact(txt)}, timeout=15)
        log.info("webhook POST -> %s", code)

if __name__ == "__main__":
    sys.exit(main())
