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

# Convenience wrapper for a shell fixture, the shape most OPV cases use.
case_sh() {
  local name="$1" content="$2" rule="$3" expect_flag="$4" expect_exit="$5"
  case_file "$name" "fixture.sh" "$content" "$rule" "$expect_flag" "$expect_exit"
}

# --- OPV001: AWS-style access key id ----------------------------------------
case_sh "OPV001: a literal AKIA-shaped access key id is flagged" \
  "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE" \
  OPV001 yes 1

case_sh "OPV001: an ASIA-shaped (temporary) access key id is flagged" \
  "export AWS_ACCESS_KEY_ID=ASIAIOSFODNN7EXAMPLE" \
  OPV001 yes 1

case_sh "OPV001: a correctly-read access key id is not flagged" \
  $'read -rs AWS_ACCESS_KEY_ID\nexport AWS_ACCESS_KEY_ID' \
  OPV001 no 0

case_sh "OPV001: a short AKIA-prefixed string that isn't the full shape is not flagged" \
  "# see AKIA123 in the vendor's own example docs" \
  OPV001 no 0

# --- OPV002: credential assigned directly instead of via read --------------
case_sh "OPV002: export SECRET=<literal> is flagged" \
  "export DB_PASSWORD='hunter2-not-a-real-password'" \
  OPV002 yes 1

case_sh "OPV002: a plain (non-export) credential assignment is flagged" \
  "SERVICE_API_KEY=\"not-a-real-value-just-shaped-like-one\"" \
  OPV002 yes 1

case_sh "OPV002: a placeholder token in a credential assignment is still flagged (still not a read)" \
  "export PULUMI_CONFIG_PASSPHRASE='<the escrowed value>'" \
  OPV002 yes 1

case_sh "OPV002: export VAR with no assignment (the read form) is not flagged" \
  $'printf "AWS_SECRET_ACCESS_KEY: "; read -rs AWS_SECRET_ACCESS_KEY; echo\nexport AWS_SECRET_ACCESS_KEY' \
  OPV002 no 0

case_sh "OPV002: re-exporting an already-read variable is not flagged" \
  'export AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY"' \
  OPV002 no 0

case_sh "OPV002: exporting a command substitution is not flagged" \
  'export API_TOKEN="$(cat token.txt)"' \
  OPV002 no 0

case_sh "OPV002: an unrelated variable assigned a literal is not flagged" \
  "export TENANT_SLUG='example-tenant'" \
  OPV002 no 0

# --- OPV002: case-insensitive name matching (snake_case/camelCase) --------
case_sh "OPV002: snake_case db_password is flagged (case-insensitive)" \
  'db_password = "hunter2-a-real-looking-value"' \
  OPV002 yes 1

case_sh "OPV002: camelCase apiToken is flagged (case-insensitive)" \
  'apiToken = "sk-realtoken-not-a-real-key-0000000000"' \
  OPV002 yes 1

# --- OPV002: default-expansion re-export still carries a literal ----------
case_sh "OPV002: \${VAR:-literal} default expansion is flagged, not excused as a re-export" \
  'export DB_PASSWORD="${DB_PASSWORD:-s3cr3t-literal-fallback}"' \
  OPV002 yes 1

# --- OPV002: non-secret-shaped boolean/flag literals are excluded ----------
case_sh "OPV002: a boolean-flag literal (true) is not flagged" \
  'FEATURE_TOKEN="true"' \
  OPV002 no 0

case_sh "OPV002: a boolean-flag literal (false), mixed case, is not flagged" \
  'FEATURE_TOKEN="False"' \
  OPV002 no 0

case_sh "OPV002: a boolean-flag literal (enabled) is not flagged" \
  "export FEATURE_ACCESS_KEY_CHECK='enabled'" \
  OPV002 no 0

# --- OPV002: positive coverage for the credential names not exercised above,
# and their case-sensitivity edges (per the case-insensitivity fix above) --
case_sh "OPV002: TOKEN (upper snake_case) is flagged" \
  "export API_TOKEN='literal-token-value-not-a-real-one'" \
  OPV002 yes 1

case_sh "OPV002: TOKEN (camelCase) is flagged" \
  "authToken = 'literal-token-value-not-a-real-one'" \
  OPV002 yes 1

case_sh "OPV002: PRIVATE_KEY (upper snake_case) is flagged" \
  "export SSH_PRIVATE_KEY='-----BEGIN RSA PRIVATE KEY-----not-real-----'" \
  OPV002 yes 1

case_sh "OPV002: PRIVATE_KEY (lower snake_case) is flagged" \
  "ssh_private_key = 'literal-key-data-not-real'" \
  OPV002 yes 1

case_sh "OPV002: CREDENTIAL (upper snake_case) is flagged" \
  "export SERVICE_CREDENTIAL='literal-credential-value-not-real'" \
  OPV002 yes 1

case_sh "OPV002: CREDENTIAL (camelCase) is flagged" \
  "myCredential = \"literal-credential-value-not-real\"" \
  OPV002 yes 1

case_sh "OPV002: ENCRYPTIONSALT (lowercase, the real Pulumi key spelling) is flagged" \
  "encryptionsalt='literal-salt-value-not-real'" \
  OPV002 yes 1

case_sh "OPV002: ENCRYPTIONSALT (mixed case) is flagged" \
  'EncryptionSalt="literal-salt-value-not-real"' \
  OPV002 yes 1

# --- OPV003: bare IPv4 host address ------------------------------------------
case_sh "OPV003: a private-range host address is flagged" \
  "ssh root@10.20.1.20" \
  OPV003 yes 1

case_sh "OPV003: a public host address is flagged" \
  "the edge host resolves at 46.225.95.167 today" \
  OPV003 yes 1

case_file "OPV003: an address in a markdown table is still flagged (Reading B: everywhere, not only fences)" \
  "fixture.md" \
  "| host | address |
| --- | --- |
| edge1 | 10.20.1.10 |" \
  OPV003 yes 1

case_sh "OPV003: loopback is not flagged" \
  "curl http://127.0.0.1:8080/health" \
  OPV003 no 0

case_sh "OPV003: the IANA TEST-NET-1 documentation range is not flagged" \
  "an example host at 192.0.2.10 for illustration" \
  OPV003 no 0

case_sh "OPV003: a common netmask literal is not flagged" \
  "netmask 255.255.255.0" \
  OPV003 no 0

case_sh "OPV003: a less-common netmask (255.255.255.128) still fires -- documented gap, not a general netmask check" \
  "subnet mask 255.255.255.128" \
  OPV003 yes 1

case_sh "OPV003: a well-known public constant (8.8.8.8) is still flagged -- Reading B is every address, not only sensitive ones" \
  "the fallback resolver is 8.8.8.8" \
  OPV003 yes 1

case_sh "OPV003: a CIDR block in architecture prose (10.0.0.0/8) is still flagged" \
  "the internal network is described as 10.0.0.0/8 in the topology diagram" \
  OPV003 yes 1

case_sh "OPV003: an out-of-range octet does not false-match a version-shaped string" \
  "built from image 999.1.2.3" \
  OPV003 no 0

case_sh "OPV003: a resolved lookup, not a literal, is not flagged" \
  'export EDGE1_IPV4="$(hcloud server describe edge1 -o json | jq -r .public_net.ipv4.ip)"' \
  OPV003 no 0

# --- OPV000: malformed suppression -------------------------------------------
case_sh "OPV000: a bare disable with no rule id or reason is flagged" \
  $'# opv-disable-next-line\nssh root@10.20.1.20' \
  OPV000 yes 1

case_sh "OPV000: a disable with a rule id but no reason is flagged" \
  $'# opv-disable-next-line OPV003\nssh root@10.20.1.20' \
  OPV000 yes 1

# --- Inline suppression actually suppresses ---------------------------------
case_sh "OPV003: a well-formed suppression with a reason clears the finding" \
  $'# opv-disable-next-line OPV003 promtool fixture: this address is the subject of the assertion\nssh root@10.20.1.20' \
  OPV003 no 0

case_sh "OPV003: the suppression above does not also raise OPV000" \
  $'# opv-disable-next-line OPV003 promtool fixture: this address is the subject of the assertion\nssh root@10.20.1.20' \
  OPV000 no 0

case_sh "OPV001: a suppressed access key id clears the finding" \
  $'# opv-disable-next-line OPV001 vendor documentation example, not a real credential\nexport AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE' \
  OPV001 no 0

# --- Path exemption via .opv-lintignore -------------------------------------
case_path_exempt() {
  printf 'templates/*\tOPV003\t# template placeholder tables\n' > .opv-lintignore
  mkdir -p templates
  printf '%s\n' "ssh root@10.20.1.20" > templates/example.md
  local out rc
  out=$(GITHUB_ACTIONS=false "$LINT" --explain --mode enforce templates/example.md 2>&1)
  rc=$?
  if printf '%s' "$out" | grep -q '\[OPV003\]'; then
    FAIL=$((FAIL + 1))
    echo "FAIL: .opv-lintignore path exemption -- OPV003 still fired"
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

# --- GITHUB_ACTIONS annotation format ----------------------------------------
annotation_case() {
  local file="fixture.sh"
  printf '%s\n' "ssh root@10.20.1.20" > "$file"
  local out rc
  out=$(GITHUB_ACTIONS=true "$LINT" --explain --mode enforce "$file" 2>&1)
  rc=$?
  if printf '%s' "$out" | grep -qE '^::error file=fixture\.sh,line=[0-9]+,title=OPV003::'; then
    PASS=$((PASS + 1))
    echo "ok   GITHUB_ACTIONS=true emits an OPV003 error annotation"
  else
    FAIL=$((FAIL + 1))
    echo "FAIL: GITHUB_ACTIONS=true emits an OPV003 error annotation -- no matching ::error line"
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
