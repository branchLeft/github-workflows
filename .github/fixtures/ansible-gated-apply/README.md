# Fixture for the ansible-gated-apply self-test

A tiny Ansible project that `ansible-gated-apply-self.yml` passes to the
reusable workflow as its `working-directory`. It holds one role with a
container-only Molecule scenario, the lint configuration the workflow expects at
the project root, the hash lock of the tools, and `stub/host.sh`, the stand-in
for a host in the plan job's dry run. Nothing here is delivered to a host.

## The lock

`ansible/requirements.in` and `ansible/requirements.txt` are a copy of the files
of the same name in `branchLeft/shared-infra` (`ansible/`), which is what the
callers of the workflow keep. `requirements.txt` is `uv pip compile` output, never
edited by hand, compiled with `--universal --python-version 3.12 --generate-hashes`
so that one file serves every platform and records a hash for every package:

```bash
cd .github/fixtures/ansible-gated-apply/ansible
uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes --output-file requirements.txt
```

A copy needs no package index, and `diff` against the file in `shared-infra` shows
that it is one. A fresh compile can differ from the copy in the transitive pins
that moved upstream since; the direct pins are the same. Dependabot reads the
fixture through its own `uv` entry in `.github/dependabot.yml`, so the lock moves
by pull request and not by hand.

The fixture uses the `ansible` package and not `ansible-core` alone. Molecule's
Docker driver needs `community.docker`, which the full package carries, so the
fixture needs no `requirements.yml` entry and no Galaxy download. That is what
lets `ansible/requirements.yml` hold only `collections: []`, the one form the
workflow's discover job accepts. The role uses `ansible.builtin` modules only, and
its scenario already sets `dependency.enabled: false`, and sets `prerun: false`
because Molecule's prerun would otherwise install role-level requirements files on
its own.

## `lint-fail/`

A second tree, beside `ansible/` and never inside it, so the real lint of the
workflow does not read it. It holds one role with one deliberate error. The
self-test job `lint-expected-fail` runs the reusable workflow's own lint steps on
it and requires the failure to be the one named in `lint-fail/README.md`.
