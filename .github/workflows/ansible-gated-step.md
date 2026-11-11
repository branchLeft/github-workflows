# ansible-gated-step.yml

The reusable sender of one routine step to one host, over the gated pull
(ANS-17). A step is a task a role keeps behind the `never` tag, run by the host's
`pull-trigger` program in step mode. The host's side is the `pull_trigger` role in
`branchLeft/shared-infra` (its README, "Step requests"); this workflow only sends
the request that role accepts and judges the one line it answers with.

`ansible-gated-apply.yml` is not changed. It cannot carry a step: its plan and
apply run only for a push, its apply runs only when a check-mode plan found a
change (a `never` step is invisible to check mode), and its answer pattern pins
`mode=apply`. A test pins those three facts (`ApplyWorkflowUntouchedTests`).

## What it does, and who must act

The owner dispatches the caller by hand and types the step id and its word. The
guard job refuses anyone else. GitHub then holds the send job for the
environment's approval, which is a second, separate act of the owner. Only then
does the send job read the step key.

| Job | Runs when | Holds |
| --- | --- | --- |
| `Guard the step request` | every call | nothing: no secret, no environment |
| `Send the step to the host (after approval)` | the guard passed | the step key, the one host and the known-hosts lines, from the environment |
| `Ansible step checks passed` | every call, even after a failure | nothing |

The guard refuses unless all of these hold: the event is `workflow_dispatch`; the
ref is the default branch; the repository is not a fork; the commit is 40
lowercase hex digits; `github.actor` and `github.triggering_actor` both equal
`owner-login` (case folded); the step is `[a-z][a-z0-9_]{0,31}`; the word is
`[A-Z0-9-]{4,32}`; and the host's inventory file at that commit lists the step
exactly once with that word. It then prints three lines to the log and to the job
summary, which is the plan for a step: `commit SHA`, `step ID` and `input PATH
sha256 HASH` (or `input none named for this step`). A check-mode run cannot show
a step, so there is no diff.

The send job checks the request again (a rerun of a failed job is started by
whoever reruns it, and the typed values go into the command), then makes one SSH
call, `ssh -- DEST "SHA STEP WORD"`, and requires one line of the form the host
prints (`mode=step`, `step=ID`) that names this commit and this step with exit
status 10 `applied` or 0 `unchanged`. It sends the same request a second time,
which must answer 0 `unchanged` with `changed=0`. The playbook output on standard
error is dropped: it stays on the host, and the job log is public. The word is a
typing guard in a public file, not a secret, and what stops a step is the
environment.

## How a repository calls it

Pin an exact tag (see the README, "Versioning"). The caller is triggered by
`workflow_dispatch` only, and its `contents: read` permission is all it needs.

```yaml
name: ansible-step

on:
  workflow_dispatch:
    inputs:
      step:
        description: The step id
        required: true
        type: string
      confirm:
        description: The word of that step in the host's inventory file
        required: true
        type: string

permissions:
  contents: read

jobs:
  step:
    uses: branchLeft/github-workflows/.github/workflows/ansible-gated-step.yml@vX.Y.Z
    with:
      step: ${{ inputs.step }}
      confirm: ${{ inputs.confirm }}
      owner-login: ${{ vars.STEP_OWNER_LOGIN }}
      step-inventory: ansible/owner_vars/ops1.yml
      step-inputs: |
        migrate=ansible/owner_vars/ops1.yml
```

The caller passes no secret. The secrets below are secrets of the environment,
which the send job reads because it runs in it, so no job outside it can.
Requiring the job `Ansible step checks passed` is optional: a dispatch is not a
pull request.

| Input | Meaning |
| --- | --- |
| `environment` | The deployment environment, default `control-plane-step` |
| `step`, `confirm` | The typed step id and word |
| `owner-login` | The one account that may dispatch |
| `step-inventory` | The host's inventory file holding `pull_trigger_steps` |
| `step-inputs` | Optional `id=path` lines: the committed input each step compares, hashed for the approver |
| `host-timeout-minutes`, `send-timeout-minutes` | Limits, default 65 and 140 |

| Secret | Meaning |
| --- | --- |
| `ssh_step_key` | The private key of the step route |
| `ssh_step_host` | Exactly one `user@host` line, the step account's: a second line is refused |
| `ssh_known_hosts` | The known-hosts lines of the host and of any jump host |
| `ssh_jump_host`, `ssh_jump_key` | Optional, both or neither: one jump hop, with a key of its own |
| `ssh_jump2_host`, `ssh_jump2_key` | Optional, both or neither, needs the first hop: a second hop |

## What the owner does once

None of this is done by this workflow or by an agent. The host half is in the
`pull_trigger` README, "What the owner does on a host, once".

1. Make the step key pair. The private half is the environment secret
   `ssh_step_key`; the public half goes in the step account's `authorized_keys`
   with the `--mode step` line of that README.
2. Add the step account, its sudoers line and the known-hosts entry on the host,
   and store the entry as the environment secret `ssh_known_hosts`.
3. Create the environment `control-plane-step` with the owner as its only required
   reviewer and deployment limited to the default branch, and store
   `ssh_step_host` in it.
4. Set the repository variable `STEP_OWNER_LOGIN` to the owner's GitHub account.
5. Merge the caller, pinned to a tag of this repository.

## Controls, and how each is shown to fail

`tools/sabotage_step.py` breaks each control in a copy of the repository and
requires the named test to go red, after a run of the unedited tree that must be
green. The self-test workflow runs it on every pull request.

| Control | Broken by | Red test |
| --- | --- | --- |
| W1 the send job has the environment | deleting its `environment:` line | `StructureTests` |
| W2 only a dispatch sends | allowing a `push` event | `RequestGuardTests` |
| W3 only the default branch | dropping the ref test | `RequestGuardTests` |
| W4 the step and the word are shaped | dropping either pattern | `RequestGuardTests`, `SendGuardTests` |
| W5 the dispatcher is the owner | dropping the actor test | `RequestGuardTests`, `SendGuardTests` |
| W6 the answer is a `mode=step` line for this step | accepting another mode, no `step=` or another id | `SendTests` |
| W7 one host | accepting two lines in `ssh_step_host` | `SendTests` |
| W8 the key is removed on every exit | deleting the trap | `SendTests` |
| W9 the second run changes nothing | accepting a change on the second answer | `SendTests` |
| W10 the apply workflow is untouched | editing its pattern or its push-only guard | `ApplyWorkflowUntouchedTests` |
| the step is on the allow-list and the word is its word | dropping either test | `InventoryTests` |
| the key is never echoed | tracing the shell or printing the key | `SendTests` |

## Named limits

- The environment's required reviewer and its deployment rule are settings of the
  repository, reviewed and not tested here. So is whether the secrets of the
  environment arrive in the send job under the names above.
- The host's closed list of step ids is the program's. This workflow reads the
  allow-list from the committed inventory file and holds no copy of the list.
- The plan shows the sha256 of the input file a caller names for a step, not the
  value inside it. For `migrate` that file holds the image digest the step uses.
- A step that is not idempotent fails the second request. That is the intent: the
  first request's effect stays, and the run is red.
- The actor check trusts the `owner-login` the caller passes, which is reviewed in
  the caller's pull request.
