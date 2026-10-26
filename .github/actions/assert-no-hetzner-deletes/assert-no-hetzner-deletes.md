# assert-no-hetzner-deletes

`assert-no-hetzner-deletes.py` reads a `pulumi preview --json` file and fails if
the plan would destroy a protected Hetzner resource. It is the one home of that
check: a repository that applies a Hetzner stack calls the composite action
beside this file and holds no copy of the script.

## Calling it

Use the composite action, never a copy of the script. The inputs are in the repo
`README.md`. The action runs, in this order and stopping at the first failure:

1. `--self-test`, the script's own decision cases, so a script that no longer
   behaves as written cannot report a plan clean.
2. `--verify-coverage`, against the caller's coverage map.
3. The plan check itself, when `preview-json` is given. A plan that fails it is
   printed in full after the error, for the person reading the run.

`program-dir` and `coverage-map` are required on every call, including the one
that checks a plan. The coverage check is not a mode a caller can leave out.

**Call it in the apply job**, after the preview that job runs itself, so the
check reads the same state `pulumi up` is about to act on, seconds earlier and
with the same credentials. A check in an earlier job reads an older plan.

## The rule

Protection is by resource type, not by logical id. A name list would miss the
children a component creates under its own URN parent chain, because the
component derives its children's names; a type matched on the leaf of the chain
covers them.

Protected: `hcloud:index/server:Server`, `hcloud:index/network:Network`,
`hcloud:index/networkSubnet:NetworkSubnet`, `hcloud:index/firewall:Firewall` and
`hcloud:index/primaryIp:PrimaryIp`. A test pins the list to exactly these, so
widening it is a visible edit to a test.

Deliberately unprotected, because deleting one detaches nothing and loses no
state, and moving a gateway or re-attaching a host has to stay an ordinary apply:
`hcloud:index/networkRoute:NetworkRoute` and
`hcloud:index/serverNetwork:ServerNetwork`.

A step whose op contains `delete` or `replace` destroys, or schedules the
destruction of, the resource it names. That covers `delete`, `delete-replaced`,
`replace`, `create-replacement`, `read-replacement`, `import-replacement`,
`discard-replaced` and `remove-pending-replace` without listing them, so a future
op name has to avoid both words to slip through.

**Anything it cannot read fails.** A missing or malformed preview, a JSON value
that is not a plan, a step without an `op` or a `urn`, and a URN that is not a
resource URN each exit 1, never 0. "I found no steps" and "there are no
destructive steps" must not share an exit code.

Exit status: 0 clean, 1 a protected resource would be destroyed or an input could
not be read, 2 a usage error.

## The coverage map

The plan check cannot see a resource that has moved out of the program: the plan
keeps passing on whatever is left. `--verify-coverage` closes that. The caller
commits a JSON file, an object from a program directory's basename to the
non-empty list of constructors that directory must declare:

```json
{
  "hosts": ["Host"],
  "demo-host": ["hcloud.Server", "hcloud.Firewall", "hcloud.PrimaryIp"]
}
```

The check fails when:

- the directory's basename is not a key (a program directory the map has never
  heard of is added to the map, not waved through);
- the directory has no `.ts` file;
- a listed constructor is not declared as `new <constructor>(` in any `.ts` file
  directly in the directory;
- the map is unreadable, is not an object, is empty, or has an empty list or a
  non-string entry. An empty list is refused rather than read as nothing to
  check.

Moving a resource out of a program therefore makes the map a reviewed edit in the
same change, because the map lives in the caller's repository beside the program.
The map is the caller's data and not this script's: the script holds no directory
name of any caller.

**Known limit: a declaration inside a comment counts.** The match is on the text
`new <constructor>(`, so a declaration commented out, with that text still in the
comment, is read as declared. A mention that is not shaped like a declaration (an
import, or a name in prose) does not count.

## What this does not prove

`pulumi preview` compares the program with Pulumi state, never with live Hetzner.
A resource already deleted out of band still reads as unchanged. The check
answers whether this apply will destroy something, not whether the estate is
intact.

## Tests

`test_assert_no_hetzner_deletes.py` runs the decision cases, the coverage map and
its refusals, the command line (the real exit codes, as a subprocess), the
action's own `run:` body under bash against a delete fixture
(`fixtures/preview-delete.json`) and a no-delete fixture
(`fixtures/preview-no-delete.json`), and the action's structure. Its
`SabotageTests` break each control in a copy and require the named tests to go
red there, then require the unmodified copy to be green. The self-test workflow
also runs the action itself over both fixtures on every pull request.
