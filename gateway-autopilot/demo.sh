#!/bin/bash
# Controlled demo in a temp dir + sandbox container on 127.0.0.1:4901. Live config is never touched.
set -u
D=/opt/litellm/autopilot-demo; rm -rf $D; mkdir -p $D; chmod 700 $D
cp /opt/litellm/autopilot/autopilot.py $D/
sed 's/^count_interval_hours:.*/count_interval_hours: 0/' /opt/litellm/autopilot/autopilot.yaml > $D/autopilot.yaml
# test config: cerebras model name -> nonexistent
sed 's#model: openai/gpt-oss-120b, api_base: "https://api.cerebras.ai/v1"#model: openai/gpt-oss-999b-nonexistent, api_base: "https://api.cerebras.ai/v1"#' /opt/litellm/litellm-config.yaml > $D/test-config.yaml
chmod 644 $D/test-config.yaml
grep -c nonexistent $D/test-config.yaml
R="python3 -B $D/autopilot.py --base $D --config $D/test-config.yaml --state $D/state.json --sandbox"
q(){ python3 -c "import json;s=json.load(open('$D/state.json'));print('RESULT',s['last_run']['result'],s['last_run']['changes'],'removed=',[r['aliases'] for r in s['removed']])"; grep -c 'gpt-oss-999b' $D/test-config.yaml | sed 's/^/  cfg lines with bad model: /'; sha256sum $D/test-config.yaml|cut -c1-12; }
echo "### day1 (baseline)"; $R --once >/dev/null; q
echo "### day2 (2nd perm -> remove)"; $R --once | grep -E 'REMOVE|validate|verify|run end|cerebras'; q
echo "### day3 (upstream recovered: simulate 200)"; $R --once --simulate gpt-oss-999b=200 | grep -E 'run end|999b'; q
echo "### day4 (2nd 200 -> readd)"; $R --once --simulate gpt-oss-999b=200 | grep -E 'validate|verify|run end'; q
diff <(sed 1d $D/test-config.yaml | python3 -c "import yaml,sys,json;print(json.dumps(yaml.safe_load(sys.stdin),sort_keys=True))") <(python3 -c "import yaml,json;print(json.dumps(yaml.safe_load(open('$D/test-config.yaml')),sort_keys=True))") >/dev/null
python3 - <<P
import yaml
a=yaml.safe_load(open('$D/test-config.yaml'))
b=yaml.safe_load(open('/opt/litellm/litellm-config.yaml'))
import json
b2=json.loads(json.dumps(b).replace('openai/gpt-oss-120b", "api_base": "https://api.cerebras.ai/v1"','openai/gpt-oss-999b-nonexistent", "api_base": "https://api.cerebras.ai/v1"'))
print("after readd, semantic equal to original test config:", a==b2)
P
echo "### day5+6 (perm again, day6 forced verify failure -> rollback)"
$R --once >/dev/null; $R --once --test-fail-verify | grep -E 'REMOVE|validate|verify|run end'; q
cat $D/sandbox-report-latest.md | head -12
ls $D/backups
docker rm -f litellm-autopilot-sandbox litellm-autopilot-validate >/dev/null 2>&1; docker ps --format '{{.Names}}'
