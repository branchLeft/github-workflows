#!/usr/bin/env bash
# Unit tests for opv-lint.sh's rules. Fixture-driven, same harness shape as
# docs-lint.test.sh: each case is a snippet fed to the script through a
# scratch git repo (opv-lint.sh requires one -- it resolves its scan root
# with `git rev-parse --show-toplevel`) and the exit code / rule presence is
# asserted.
#
# Usage: tools/opv-lint.test.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LINT="$HERE/opv-lint.sh"

WORK=$(mktemp -d) || exit 2
trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || exit 2
git init -q
git config user.email test@example.com
git config user.name test

PASS=0
FAIL=0

# case name, fixture filename, fixture content, rule id, expect-rule-flagged (yes|no), expect-exit (0|1)
case_file() {
  local name="$1" file="$2" content="$3" rule="$4" expect_flag="$5" expect_exit="$6"
  printf '%s\n' "$content" > "$file"
  local out rc
  # Force plain-text report format regardless of the ambient environment:
  # under GITHUB_ACTIONS=true opv-lint.sh emits `::error ...title=OPV001::`
  # workflow-command annotations instead, which this harness doesn't parse.
  out=$(GITHUB_ACTIONS=false "$LINT" --explain --mode enforce "$file" 2>&1)
  rc=$?
  local has_flag=no
  printf '%s' "$out" | grep -q "\[$rule\]" && has_flag=yes

  local ok=1
  if [ "$has_flag" != "$expect_flag" ]; then
    ok=0
    echo "FAIL: $name -- expected $rule=$expect_flag, got $has_flag"
    echo "$out" | sed 's/^/    /'
  fi
  if [ "$rc" != "$expect_exit" ]; then
    ok=0
    echo "FAIL: $name -- expected exit=$expect_exit, got $rc"
  fi
  if [ "$ok" = "1" ]; then
    PASS=$((PASS + 1))
    echo "ok   $name"
  else
    FAIL=$((FAIL + 1))
  fi
  rm -f "$file"
}

# Convenience wrapper for a shell fixture, the shape most non-placeholder
# OPV cases use.
case_sh() {
  local name="$1" content="$2" rule="$3" expect_flag="$4" expect_exit="$5"
  case_file "$name" "fixture.sh" "$content" "$rule" "$expect_flag" "$expect_exit"
}

# Convenience wrapper for OPV002 (placeholder) cases, which need a markdown
# file with a real fenced code block -- OPV002 is scoped to bash/sh/shell/
# console fences only, unlike every other rule here.
case_md_fence() {
  local name="$1" lang="$2" body="$3" rule="$4" expect_flag="$5" expect_exit="$6"
  local content
  content=$(printf '## fixture\n\n```%s\n%s\n```\n' "$lang" "$body")
  case_file "$name" "fixture.md" "$content" "$rule" "$expect_flag" "$expect_exit"
}

# ==== OPV001: a committed concrete operational value =========================
# ---- address half (IPv4 literal / private subnet) --------------------------
case_sh "OPV001: a private-range host address is flagged" \
  "ssh root@10.20.1.20" \
  OPV001 yes 1

case_sh "OPV001: a public host address is flagged" \
  "the edge host resolves at 46.225.95.167 today" \
  OPV001 yes 1

case_file "OPV001: an address in a markdown table is still flagged (Reading B: everywhere, not only fences)" \
  "fixture.md" \
  "| host | address |
| --- | --- |
| edge1 | 10.20.1.10 |" \
  OPV001 yes 1

case_sh "OPV001: loopback is not flagged" \
  "curl http://127.0.0.1:8080/health" \
  OPV001 no 0

case_sh "OPV001: the IANA TEST-NET-1 documentation range is not flagged" \
  "an example host at 192.0.2.10 for illustration" \
  OPV001 no 0

case_sh "OPV001: a common netmask literal is not flagged" \
  "netmask 255.255.255.0" \
  OPV001 no 0

case_sh "OPV001: a less-common netmask (255.255.255.128) still fires -- documented gap, not a general netmask check" \
  "subnet mask 255.255.255.128" \
  OPV001 yes 1

case_sh "OPV001: a well-known public constant (8.8.8.8) is still flagged -- Reading B is every address, not only sensitive ones" \
  "the fallback resolver is 8.8.8.8" \
  OPV001 yes 1

case_sh "OPV001: a CIDR block in architecture prose (10.0.0.0/8) is still flagged" \
  "the internal network is described as 10.0.0.0/8 in the topology diagram" \
  OPV001 yes 1

case_sh "OPV001: an out-of-range octet does not false-match a version-shaped string" \
  "built from image 999.1.2.3" \
  OPV001 no 0

case_sh "OPV001: a resolved lookup, with the hostname itself parameterized too, is not flagged" \
  'export HOST_IPV4="$(hcloud server describe "$HOST_NAME" -o json | jq -r .public_net.ipv4.ip)"' \
  OPV001 no 0

case_sh "OPV001: naming which host to look up is still a literal hostname and IS flagged, even though the address itself is resolved" \
  'export EDGE1_IPV4="$(hcloud server describe edge1 -o json | jq -r .public_net.ipv4.ip)"' \
  OPV001 yes 1

# ---- hostname half (the estate's own fixed operational names) -------------
case_sh "OPV001: the bare hostname db1 in prose is flagged" \
  "All three commands run against db1, which has no public address." \
  OPV001 yes 1

case_sh "OPV001: the bare hostname edge1 as an hcloud resource name is flagged" \
  "IPV4=\$(hcloud server describe edge1 -o json | jq -r .public_net.ipv4.ip)" \
  OPV001 yes 1

case_sh "OPV001: mx1 is flagged" \
  "the mail host is mx1" \
  OPV001 yes 1

case_sh "OPV001: app1 is flagged" \
  "app1 hosts the tenant containers" \
  OPV001 yes 1

case_sh "OPV001: a hyphenated compound (hetzner-edge1) still catches the bare name" \
  "see hetzner-edge1 for the host record" \
  OPV001 yes 1

case_sh "OPV001: edge10 does NOT false-match edge1 (word-bounded)" \
  "capacity target is edge10 servers" \
  OPV001 no 0

case_sh "OPV001: db1-mysql-bin (a binlog naming convention, not a hostname reference) still catches the bare name" \
  "rotate db1-mysql-bin.000123" \
  OPV001 yes 1

# ==== OPV002: an unsubstituted placeholder in a copy-pasteable command ======
# ---- hyphen/dot-shaped tokens (the estate's own most frequent examples) ---
case_md_fence "OPV002: a hyphenated placeholder (<edge1-ipv4>) in a bash fence is flagged" \
  "bash" "ssh root@<edge1-ipv4>" \
  OPV002 yes 1

case_md_fence "OPV002: <mail-host>, the single most frequent measured token, is flagged" \
  "sh" "dig +short <mail-host>" \
  OPV002 yes 1

case_md_fence "OPV002: a dotted placeholder (<tenant.slug>) is flagged" \
  "shell" "curl https://<tenant.slug>/health" \
  OPV002 yes 1

# ---- bare single-word forms from the fixed list ----------------------------
case_md_fence "OPV002: the bare form <host> is flagged" \
  "bash" "ssh root@<host>" \
  OPV002 yes 1

case_md_fence "OPV002: the bare form <secret> is flagged" \
  "console" "export API_KEY=<secret>" \
  OPV002 yes 1

case_md_fence "OPV002: a bare word NOT in the fixed list is not flagged (known limitation)" \
  "bash" "echo <widget>" \
  OPV002 no 0

# ---- exclusions: redirection, comparison, heredoc, HTML -------------------
case_md_fence "OPV002: shell redirection (sort < input.txt) is not flagged" \
  "bash" "sort < input.txt > output.txt" \
  OPV002 no 0

case_md_fence "OPV002: a numeric comparison (a < b) is not flagged" \
  "bash" 'if [ "$a" -lt "$b" ]; then echo yes; fi' \
  OPV002 no 0

case_md_fence "OPV002: a heredoc opener (<<EOF) is not flagged" \
  "bash" $'cat <<EOF\nhello\nEOF' \
  OPV002 no 0

case_md_fence "OPV002: an HTML/generic tag (<div class=\"row\">) is not flagged" \
  "bash" '# not real shell, but shaped like markup: <div class="row">' \
  OPV002 no 0

case_md_fence "OPV002: a TypeScript generic (Map<string, number>) is not flagged" \
  "bash" "# Map<string, number> mentioned in a comment" \
  OPV002 no 0

# ---- scope: command fences ONLY, not prose, not other-language fences ----
# <mail-host>, not <edge1-ipv4>, so these cases isolate OPV002's own scope
# boundary without also tripping OPV001's unrelated hostname match on
# "edge1" (a real, separate finding these fixtures aren't testing for).
case_file "OPV002: the same token in prose (no fence) is not flagged -- opposite scope from OPV001/003/004" \
  "fixture.md" \
  "Run the step with your own value in place of <mail-host>." \
  OPV002 no 0

case_md_fence "OPV002: the same token in a non-command fence (yaml) is not flagged" \
  "yaml" "host: <mail-host>" \
  OPV002 no 0

case_sh "OPV002: the same token in a .sh file (not markdown) is not flagged -- scoped to markdown fences" \
  "ssh root@<mail-host>" \
  OPV002 no 0

# ==== OPV003: an AWS-style access key id ====================================
case_sh "OPV003: a literal AKIA-shaped access key id is flagged" \
  "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE" \
  OPV003 yes 1

case_sh "OPV003: an ASIA-shaped (temporary) access key id is flagged" \
  "export AWS_ACCESS_KEY_ID=ASIAIOSFODNN7EXAMPLE" \
  OPV003 yes 1

case_sh "OPV003: a correctly-read access key id is not flagged" \
  $'read -rs AWS_ACCESS_KEY_ID\nexport AWS_ACCESS_KEY_ID' \
  OPV003 no 0

case_sh "OPV003: a short AKIA-prefixed string that isn't the full shape is not flagged" \
  "# see AKIA123 in the vendor's own example docs" \
  OPV003 no 0

# ==== OPV004: a credential-shaped variable assigned a literal ===============
case_sh "OPV004: export SECRET=<literal> is flagged" \
  "export DB_PASSWORD='hunter2-not-a-real-password'" \
  OPV004 yes 1

case_sh "OPV004: a plain (non-export) credential assignment is flagged" \
  "SERVICE_API_KEY=\"not-a-real-value-just-shaped-like-one\"" \
  OPV004 yes 1

case_sh "OPV004: a placeholder token in a credential assignment is still flagged (still not a read)" \
  "export PULUMI_CONFIG_PASSPHRASE='<the escrowed value>'" \
  OPV004 yes 1

case_sh "OPV004: export VAR with no assignment (the read form) is not flagged" \
  $'printf "AWS_SECRET_ACCESS_KEY: "; read -rs AWS_SECRET_ACCESS_KEY; echo\nexport AWS_SECRET_ACCESS_KEY' \
  OPV004 no 0

case_sh "OPV004: re-exporting an already-read variable is not flagged" \
  'export AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY"' \
  OPV004 no 0

case_sh "OPV004: exporting a command substitution is not flagged" \
  'export API_TOKEN="$(cat token.txt)"' \
  OPV004 no 0

case_sh "OPV004: an unrelated variable assigned a literal is not flagged" \
  "export TENANT_SLUG='example-tenant'" \
  OPV004 no 0

# ---- case-insensitive name matching (snake_case/camelCase) -----------------
case_sh "OPV004: snake_case db_password is flagged (case-insensitive)" \
  'db_password = "hunter2-a-real-looking-value"' \
  OPV004 yes 1

case_sh "OPV004: camelCase apiToken is flagged (case-insensitive)" \
  'apiToken = "sk-realtoken-not-a-real-key-0000000000"' \
  OPV004 yes 1

# ---- default-expansion re-export still carries a literal -------------------
case_sh "OPV004: \${VAR:-literal} default expansion is flagged, not excused as a re-export" \
  'export DB_PASSWORD="${DB_PASSWORD:-s3cr3t-literal-fallback}"' \
  OPV004 yes 1

# ---- non-secret-shaped boolean/flag literals are excluded ------------------
case_sh "OPV004: a boolean-flag literal (true) is not flagged" \
  'FEATURE_TOKEN="true"' \
  OPV004 no 0

case_sh "OPV004: a boolean-flag literal (false), mixed case, is not flagged" \
  'FEATURE_TOKEN="False"' \
  OPV004 no 0

case_sh "OPV004: a boolean-flag literal (enabled) is not flagged" \
  "export FEATURE_ACCESS_KEY_CHECK='enabled'" \
  OPV004 no 0

# ---- positive coverage for the credential names not exercised above, and
# their case-sensitivity edges ------------------------------------------------
case_sh "OPV004: TOKEN (upper snake_case) is flagged" \
  "export API_TOKEN='literal-token-value-not-a-real-one'" \
  OPV004 yes 1

case_sh "OPV004: TOKEN (camelCase) is flagged" \
  "authToken = 'literal-token-value-not-a-real-one'" \
  OPV004 yes 1

case_sh "OPV004: PRIVATE_KEY (upper snake_case) is flagged" \
  "export SSH_PRIVATE_KEY='-----BEGIN RSA PRIVATE KEY-----not-real-----'" \
  OPV004 yes 1

case_sh "OPV004: PRIVATE_KEY (lower snake_case) is flagged" \
  "ssh_private_key = 'literal-key-data-not-real'" \
  OPV004 yes 1

case_sh "OPV004: CREDENTIAL (upper snake_case) is flagged" \
  "export SERVICE_CREDENTIAL='literal-credential-value-not-real'" \
  OPV004 yes 1

case_sh "OPV004: CREDENTIAL (camelCase) is flagged" \
  "myCredential = \"literal-credential-value-not-real\"" \
  OPV004 yes 1

case_sh "OPV004: ENCRYPTIONSALT (lowercase, the real Pulumi key spelling) is flagged" \
  "encryptionsalt='literal-salt-value-not-real'" \
  OPV004 yes 1

case_sh "OPV004: ENCRYPTIONSALT (mixed case) is flagged" \
  'EncryptionSalt="literal-salt-value-not-real"' \
  OPV004 yes 1

# ==== OPV000: malformed suppression =========================================
case_sh "OPV000: a bare disable with no rule id or reason is flagged" \
  $'# opv-disable-next-line\nssh root@10.20.1.20' \
  OPV000 yes 1

case_sh "OPV000: a disable with a rule id but no reason is flagged" \
  $'# opv-disable-next-line OPV001\nssh root@10.20.1.20' \
  OPV000 yes 1

# ==== Inline suppression actually suppresses ================================
case_sh "OPV001: a well-formed suppression with a reason clears the finding" \
  $'# opv-disable-next-line OPV001 promtool fixture: this address is the subject of the assertion\nssh root@10.20.1.20' \
  OPV001 no 0

case_sh "OPV001: the suppression above does not also raise OPV000" \
  $'# opv-disable-next-line OPV001 promtool fixture: this address is the subject of the assertion\nssh root@10.20.1.20' \
  OPV000 no 0

case_sh "OPV003: a suppressed access key id clears the finding" \
  $'# opv-disable-next-line OPV003 vendor documentation example, not a real credential\nexport AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE' \
  OPV003 no 0

# ==== The two exemption cases named verbatim in the ruling ===================
# 1. A test fixture where the hostname/address IS the subject of the
#    assertion (a promtool alert-rule fixture, an address-plan-drift test, a
#    render.ts unit test). Threading it through a lookup would make the test
#    assert on whatever the environment happens to hold, which inverts what
#    a fixture is for.
case_file "OPV001: a promtool-style test fixture hostname is flagged by default (no path carve-out)" \
  "alert_rules_test.yml" \
  "- alert: TargetDown
  expr: up{instance=\"db1:9100\"} == 0" \
  OPV001 yes 1

case_file "OPV001: the same fixture, with the ruling's own inline exemption wording, is cleared" \
  "alert_rules_test.yml" \
  $'- alert: TargetDown\n  # opv-disable-next-line OPV001 this hostname is the subject of the assertion; threading it would make the test assert on the environment\n  expr: up{instance="db1:9100"} == 0' \
  OPV001 no 0

# 2. The tenant template's <generated-repo> -- no command can resolve it
#    because the repository does not exist until the template is
#    instantiated, so it is exempted rather than made to fail forever.
case_md_fence "OPV002: <generated-repo> is flagged by default (no path carve-out)" \
  "bash" 'pulumi login "$(gh variable get PULUMI_BACKEND_URL --repo branchLeft/<generated-repo>)"' \
  OPV002 yes 1

case_file "OPV002: <generated-repo>, with the ruling's own inline exemption wording, is cleared" \
  "fixture.md" \
  $'## fixture\n\n```bash\n# opv-disable-next-line OPV002 no command can resolve this; the repository does not exist until the template is instantiated\npulumi login "$(gh variable get PULUMI_BACKEND_URL --repo branchLeft/<generated-repo>)"\n```\n' \
  OPV002 no 0

# ==== Path exemption via .opv-lintignore =====================================
case_path_exempt() {
  printf 'templates/*\tOPV001\t# template placeholder tables\n' > .opv-lintignore
  mkdir -p templates
  printf '%s\n' "ssh root@10.20.1.20" > templates/example.md
  local out rc
  out=$(GITHUB_ACTIONS=false "$LINT" --explain --mode enforce templates/example.md 2>&1)
  rc=$?
  if printf '%s' "$out" | grep -q '\[OPV001\]'; then
    FAIL=$((FAIL + 1))
    echo "FAIL: .opv-lintignore path exemption -- OPV001 still fired"
    echo "$out" | sed 's/^/    /'
  elif [ "$rc" != "0" ]; then
    FAIL=$((FAIL + 1))
    echo "FAIL: .opv-lintignore path exemption -- expected exit 0, got $rc"
  else
    PASS=$((PASS + 1))
    echo "ok   .opv-lintignore path exemption suppresses the matching rule"
  fi
  rm -rf templates .opv-lintignore
}
case_path_exempt

# ==== GITHUB_ACTIONS annotation format =======================================
annotation_case() {
  local file="fixture.sh"
  printf '%s\n' "ssh root@10.20.1.20" > "$file"
  local out rc
  out=$(GITHUB_ACTIONS=true "$LINT" --explain --mode enforce "$file" 2>&1)
  rc=$?
  if printf '%s' "$out" | grep -qE '^::error file=fixture\.sh,line=[0-9]+,title=OPV001::'; then
    PASS=$((PASS + 1))
    echo "ok   GITHUB_ACTIONS=true emits an OPV001 error annotation"
  else
    FAIL=$((FAIL + 1))
    echo "FAIL: GITHUB_ACTIONS=true emits an OPV001 error annotation -- no matching ::error line"
    echo "$out" | sed 's/^/    /'
  fi
  if [ "$rc" != "1" ]; then
    FAIL=$((FAIL + 1))
    echo "FAIL: GITHUB_ACTIONS=true exit code -- expected 1, got $rc"
  fi
  rm -f "$file"
}
annotation_case

# Mode resolution (.opv-lint.mode, warn-vs-enforce, the ENFORCED file set) is
# shared, unmodified logic sourced from lint-common.sh; every case above
# forces --mode enforce so it isn't exercised here, the same choice
# docs-lint.test.sh makes for the identical mechanism under its own mode
# file.

echo
echo "opv-lint.test.sh: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
