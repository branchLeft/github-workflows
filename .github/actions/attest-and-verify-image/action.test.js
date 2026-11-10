// The attest-and-verify action signs what a repository pushes, and the check
// that says the signature is genuine is only worth anything if it names the
// right identity. These checks pin the properties that keep it honest for every
// calling repository: the identity inputs are required and never defaulted, the
// attestation is made, then verified twice, then tried with a wrong identity,
// the scripts are read from this directory (not from the caller's workspace),
// the verify script forwards every identity flag, and every action used is
// pinned to a full commit sha. Plain node:test, no dependency to install.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const DIR = import.meta.dirname;
const read = (file) => fs.readFileSync(path.join(DIR, file), 'utf-8');
const uncommentedText = (text) => text.replace(/^[ \t]*#.*$/gm, '');
const uncommented = (file) => uncommentedText(read(file));

const action = uncommented('action.yml');
const apiAt = action.indexOf('Verify the attestation through the GitHub API');
const ociAt = action.indexOf('Verify the attestation from the registry');
const sabotageAt = action.indexOf('Prove a wrong identity is rejected');

// The text of one input: from its `  name:` line to the next input or `runs:`.
function inputBlock(name) {
  const inputsAt = action.indexOf('\ninputs:\n');
  const runsAt = action.indexOf('\nruns:\n');
  assert.ok(inputsAt > -1 && runsAt > inputsAt, 'the action has inputs and runs sections');
  const inputs = action.slice(inputsAt + 1, runsAt);
  const start = inputs.search(new RegExp(`^  ${name}:\\n`, 'm'));
  assert.ok(start > -1, `the action has an input named ${name}`);
  const rest = inputs.slice(start + 1);
  const next = rest.search(/^ {2}[a-z][\w-]*:\n/m);
  return next === -1 ? inputs.slice(start) : inputs.slice(start, start + 1 + next);
}

test('the identity inputs are required and have no default, so a caller must state them', () => {
  for (const name of ['signer-workflow', 'repo', 'image', 'digest']) {
    const block = inputBlock(name);
    assert.match(block, /required: true/, `${name} is required`);
    assert.doesNotMatch(block, /default:/, `${name} has no default`);
  }
  assert.doesNotMatch(
    action,
    /workflow_ref|job_workflow_ref|github\.repository|github\.workflow/,
    'no identity is read from the run being checked'
  );
});

test('the action attests the digest with the registry copy stored and nothing else granted', () => {
  assert.match(action, /uses: actions\/attest-build-provenance@[0-9a-f]{40} # v\d+\.\d+\.\d+/);
  assert.match(action, /subject-name: \$\{\{ inputs\.image \}\}/);
  assert.match(action, /subject-digest: \$\{\{ inputs\.digest \}\}/);
  assert.match(action, /push-to-registry: true/);
  assert.match(action, /create-storage-record: false/);
  assert.doesNotMatch(action, /artifact-metadata/);
});

test('the action verifies twice with the real identity, then requires a wrong one to be refused', () => {
  const attest = action.indexOf('actions/attest-build-provenance@');
  assert.ok(attest > -1 && apiAt > attest && ociAt > apiAt && sabotageAt > ociAt);
  const sabotageStep = action.slice(sabotageAt);
  assert.match(
    sabotageStep,
    /"\$ACTION_DIR\/prove-wrong-identity-rejected\.sh" "\$IMAGE@\$DIGEST" "\$REPO" "\$SIGNER_WORKFLOW"/,
    'the wrong-identity check is the script, called with the real identity'
  );
  assert.doesNotMatch(sabotageStep, /\.sabotage/, 'the wrong identity is built in the script');
  assert.doesNotMatch(action, /\|\| true|continue-on-error/, 'no failure is swallowed');
  assert.doesNotMatch(action, /\n {4}if: |\n {6}if: /, 'no step can be skipped by a condition');
});

test('each real-identity verify retries five times, with waits totalling about two minutes', () => {
  const apiStep = action.slice(apiAt, ociAt);
  const ociStep = action.slice(ociAt, sabotageAt);
  for (const step of [apiStep, ociStep]) {
    assert.match(step, /for attempt in 1 2 3 4 5; do/, 'five attempts');
    assert.match(
      step,
      /pause=\$\(\(attempt \* 12\)\)/,
      'the wait grows each attempt: 12, 24, 36, 48 seconds'
    );
    assert.match(step, /VERIFY_PLAIN_ERRORS="\$plain"/, 'earlier attempts are plain text');
    assert.match(step, /echo "Verify .*attempt \$attempt of 5"/, 'each attempt is logged');
    assert.match(step, /::error::[^\n]*in 5 attempts"\n {8}exit 1/, 'the last failure is red');
    assert.match(
      step,
      /"\$ACTION_DIR\/verify-image-attestation\.sh" "\$IMAGE@\$DIGEST" "\$REPO" "\$SIGNER_WORKFLOW"/,
      'the verify is the one script, called with the real identity'
    );
  }
  assert.match(ociStep, /--bundle-from-oci/);
  assert.doesNotMatch(apiStep, /--bundle-from-oci/, 'the first verify goes through the API');
});

test('the scripts are read from the action directory, never from the caller workspace', () => {
  assert.doesNotMatch(action, /GITHUB_WORKSPACE|\.\/scripts\//, 'no path into the caller');
  const envLines = action.match(/ACTION_DIR: \$\{\{ github\.action_path \}\}/g) ?? [];
  assert.equal(envLines.length, 3, 'each of the three script steps sets ACTION_DIR');
  assert.doesNotMatch(action, /uses: \.\//, 'no local action: it would resolve in the caller');
  for (const script of ['verify-image-attestation.sh', 'prove-wrong-identity-rejected.sh']) {
    const mode = fs.statSync(path.join(DIR, script)).mode;
    assert.ok(mode & 0o100, `${script} is executable`);
  }
});

test('the action reads inputs through env, never into the script text', () => {
  const runBlocks = [...action.matchAll(/run: \|\n((?: {8}.*\n?)+)/g)].map((m) => m[1]);
  assert.ok(runBlocks.length >= 4);
  for (const block of runBlocks) {
    assert.doesNotMatch(block, /\$\{\{/, 'no expression inside a run script');
  }
});

test('every action used is pinned to a full commit sha', () => {
  const uses = [...action.matchAll(/^\s*(?:- )?uses: (\S.*)$/gm)].map((m) => m[1]);
  assert.ok(uses.length > 0, 'the action uses an action');
  for (const line of uses) {
    assert.match(line, /^[\w.-]+\/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$/, `pinned: ${line}`);
  }
});

test('the wrong-identity script refuses by identity through both paths and never by a fault', () => {
  const script = read('prove-wrong-identity-rejected.sh');
  assert.match(script, /WRONG="\$3\.sabotage"/, 'the wrong identity is the signer with .sabotage');
  assert.match(script, /VERIFY_PLAIN_ERRORS=1/, 'the expected refusal is plain text');
  assert.match(
    script,
    /"\$VERIFY" "\$IMAGE_REF" "\$REPO" "\$WRONG" \$2/,
    'the second path adds --bundle-from-oci'
  );
  assert.match(script, /--bundle-from-oci/);
  assert.match(
    script,
    /no matching CertificateIdentity\|Subject Alternative Name\|Policy verification failed/
  );
  assert.match(script, /accepted \$1: the verify check is vacuous/, 'an acceptance fails the job');
  assert.match(script, /VERIFY="\$HERE\/verify-image-attestation\.sh"/, 'it uses the one verifier');
});

test('the verify script fixes every identity flag and refuses a non-digest reference', () => {
  const script = read('verify-image-attestation.sh');
  for (const flag of [
    '--repo "$REPO"',
    '--signer-workflow "$SIGNER_WORKFLOW"',
    '--source-ref refs/heads/main',
    '--cert-oidc-issuer https://token.actions.githubusercontent.com',
    '--deny-self-hosted-runners',
  ]) {
    assert.ok(script.includes(flag), `the script passes ${flag}`);
  }
  assert.ok(script.includes('@sha256:[0-9a-f]{64}$'), 'the script requires a digest reference');
  assert.match(
    script,
    /"\$REPO"\/\.github\/workflows\/\?\*\) ;;/,
    'the signer must be a workflow file of the named repository'
  );
});

test('the verify script holds the placeholder token name and value on separate lines', () => {
  const script = uncommented('verify-image-attestation.sh');
  assert.match(script, /^ {4}AUTH_VAR="GH_TOKEN"$/m);
  assert.match(script, /^ {4}AUTH_GATE="placeholder-not-a-credential"$/m);
  assert.match(script, /^ {4}export "\$AUTH_VAR=\$AUTH_GATE"$/m);
  for (const [number, line] of script.split('\n').entries()) {
    assert.doesNotMatch(
      line,
      /(GH_TOKEN|GITHUB_TOKEN)["']?=/,
      `line ${number + 1} assigns a literal to the token name`
    );
  }
});
