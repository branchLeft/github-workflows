# assert-environment-gated

`assert-environment-gated.py` exits 0 only if a GitHub environment really gates
its deploy job. It is the one home of that check: a repository with a deploy job
calls the composite action beside this file and holds no copy of the script.

## Calling it

Use the composite action, never a copy of the script. The inputs are in the repo
`README.md`. The action makes the two API reads itself, runs the script's
`--self-test`, then the real check, and fails the step on any refusal or any
read that did not succeed.

**Put it first in the deploy job, right after checkout and before any step that
reads a secret.** The job has already been admitted by the time its first step
runs, so the check can only stop what comes after it. A deploy job whose
environment gates nothing runs with no pause and no message, which is what
happens if the environment's last rule is removed, or the reviewer App is
suspended or uninstalled.

**Pin the commit, not the tag, and not a branch.** The action runs the script
from the revision the caller resolved, so a change to this repository cannot
rewrite the check that guards a deploy unless the caller's pin moves. That is the
reason a caller can drop its own copy and still trust the check: the pin is
reviewed in the caller's own pull request, as the diff of this directory.

## What counts as gated

Either of:

- `GET environments/{env}` has a `protection_rules[]` entry with
  `type == "required_reviewers"`; or
- `GET environments/{env}/deployment_protection_rules` has a
  `custom_deployment_protection_rules[]` entry with `enabled == true`,
  `app.id == 5090756` and `app.slug == "branchleft-reviewer"` (both must match).

A custom deployment protection rule is not listed in `protection_rules[]`
(recorded from the live API), so the second endpoint has to be read as well.
Either read failing, a body that is not JSON, or an error body is **not gated**:
the step fails closed.

Recorded shape of the second endpoint:

```json
{"total_count":1,"custom_deployment_protection_rules":[{"id":1,"enabled":true,"app":{"id":5090756,"slug":"branchleft-reviewer","integration_url":"https://api.github.com/apps/branchleft-reviewer"}}]}
```

## What the action adds around the script

The script reads two files. The action reads them from the API for the caller,
with these refusals before any call is made:

- `environment` must start with a letter, digit or underscore and hold only
  letters, digits, dot, underscore and hyphen. It goes into an API path, so a
  slash, a space, a query string or a name that is only dots is refused.
- `repository` must be `owner/name`, by the same rule.

It uses the token it is given, which defaults to the run's own and needs nothing
wider. Its two temporary files are removed on every way out.

## What it does not do

It does not decide who approves. A required reviewer who approves anything
satisfies it. It only stops the job from running ungated.

A private repository on a plan without environment protection cannot carry
either rule, so the step refuses its deploys. That is deliberate: the refusal is
the safe default, and lifting it for such a repository is a decision, not a
default.

## Tests

`test_assert_environment_gated.py` runs the script's command line as CI does and
checks the real exit code, for each shape of answer. `test_assert_environment_gated_action.py`
pins the script's hash, runs the action's own `run:` body under bash with a stub
`gh`, and checks the action's structure. Its `Sabotage` class breaks each control
in a copy and requires the named tests to go red there, then requires the
unmodified copy to be green. The self-test workflow also runs the action itself,
against a stub `gh` on the runner's path, for a gated and an ungated environment.
