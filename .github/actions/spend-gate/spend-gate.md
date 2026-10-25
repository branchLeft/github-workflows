# spend-gate

`spend-gate.py` reads a `pulumi preview --json` file and says whether applying
it could cost money. It writes `verdict=FREE` or `verdict=PAID` and a one-line
`reason=` to the step output, and the same line to the job summary. It gives a
verdict and nothing else: it applies nothing, approves nothing and is wired into
no workflow yet.

## Calling it

Use the composite action beside this file, never a copy of the script. The inputs
and outputs are in the repo `README.md`. The action passes the script the output
and summary paths itself.

**An unset `verdict` means PAID.** The action fails the step if the verdict cannot
be written, and a script that cannot start (for example, the sibling parser is
missing) writes nothing. A caller must test `verdict == 'FREE'`, never
`verdict != 'PAID'`.

**The gate reads the JSON, not the exit code of `pulumi preview`.** A preview that
errors exits non-zero and still writes JSON; the gate treats such a file as PAID
(see below), but the caller must also treat a failed preview step as PAID, since a
preview that wrote nothing gives the gate nothing to read.

## The rule

FREE only when every resource step that changes anything is of a free type.
Everything else is PAID. A wrong PAID costs one owner approval; a wrong FREE
costs money, so the script can only err towards PAID.

FREE needs all of these:

- The preview parsed (duplicate keys, `NaN`, over-deep nesting and wrong shapes are
  refused), has a non-empty `steps` list and has no unknown top-level or step key.
- `diagnostics` is null or empty and `maybeCorrupt` is absent or false. A preview
  that errors (a bad provider token, say) exits 1, carries one `error` diagnostic and
  lists only the `pulumi:pulumi:Stack` step, which is a free type: without this rule
  that preview would read as FREE.
- `changeSummary` is an object of non-negative counts. Its non-`same` total is no
  greater than the number of listed non-`same` steps (a change that is counted and
  not listed cannot be judged), and it is not zero while a changing step is listed.
- Every changing step has an op in `create`, `update`, `replace`,
  `create-replacement`, `delete`, `delete-replaced`, `import`,
  `import-replacement`, and its full type string is on the allowlist.

Anything else is PAID: an unknown op (`refresh`, `read`, `discard`,
`remove-pending-replace`), an unknown type, a step whose `oldState` or `newState`
names a different type from its URN, a delete or replace of a paid type, and any
error. A `same` step that carries its own evidence of a change (a non-empty
`detailedDiff`, `diffReasons` or `replaceReasons`, differing states) is a changing
step and is judged by its type. A `same` step with none is not a change.

## The allowlist

Matched on the **full type string**. A resource under a component has a type like
`parent:Type$child:Type`, which is never on the list, so it is PAID: the gate does
not look through a component it does not know.

- `pulumi:pulumi:Stack`
- `pulumi:providers:<one package segment>`
- `hcloud:index/network:Network`
- `hcloud:index/networkSubnet:NetworkSubnet`
- `hcloud:index/networkRoute:NetworkRoute`
- `hcloud:index/firewall:Firewall`
- `hcloud:index/firewallAttachment:FirewallAttachment`
- `hcloud:index/sshKey:SshKey`
- `hcloud:index/placementGroup:PlacementGroup`

The tokens come from the public hcloud provider schema. `test_spend_gate.py` pins
the list to exactly these, so widening it is a visible edit to a test.

**Not on the list, so PAID**, whatever the owner may later extend: servers and
their network attachments, volumes and attachments, load balancers and their
parts, primary and floating IPs, snapshots, storage boxes, DNS zones and records,
certificates, and every object-storage bucket (the hcloud provider has no bucket
type; buckets arrive as another provider's type and are PAID as unknown).

## The reason line

Built only from fixed text and type or op names that pass `plan-has-changes`'
sanitiser (plain characters, at most 80 long, at most 20 shown); anything odd is
shown as `<odd name>`. No value from the preview reaches the output, so a preview
cannot add a line to `GITHUB_OUTPUT`. The reason is written before the verdict in
one write.

## Shared code

The parser is `plan-has-changes`' (`_load`, `_parse_urn`, `_names`, `_is_count`,
`_check_keys`, the key sets, `_same_step_differs`, `Unclear` and the output-path
checks), loaded by path from `../plan-has-changes/plan-has-changes.py`. Nothing is
copied, and `plan-has-changes` is not edited. The cost is a dependence on its
private names: a rename there turns this action's tests red. The follow-up is a
shared `pulumi_preview` module.

## What this does not prove

The gate judges a preview, which compares the program with the state, not with
reality. A resource that already exists and is billing is not a change and is not
seen. Resource types the provider bills for in ways the list does not know are PAID
only because they are not on the list: the list is a statement of what is known
free, and the right way to extend it is to read the provider's pricing.
