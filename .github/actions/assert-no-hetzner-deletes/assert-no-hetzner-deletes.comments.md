# Comment narrative for `assert-no-hetzner-deletes.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 37-41)

```text
# Any Pulumi step op containing either word destroys, or schedules the
# destruction of, the resource it names. Covers `delete`, `delete-replaced`,
# `replace`, `create-replacement`, `read-replacement`, `import-replacement`,
# `discard-replaced` and `remove-pending-replace` without enumerating them, so
# a future op name has to actively avoid both words to slip through.
```

## Block 2 (source lines 46-53)

```text
    """Return (type token, logical name) from a Pulumi URN.

    A URN is `urn:pulumi:<stack>::<project>::<type>::<name>`, and `<type>` is
    itself `::`-free while a parent chain would appear as `<parent>$<type>`.
    Splitting from the right is therefore correct for both shapes -- and the
    parent-chain shape is the normal one where a `Host` component parents its
    server, firewall and primary IPs.
    """
```

## Block 3 (source lines 61-66)

```text
    """Return (name, op) for every protected resource this plan would destroy.

    Raises ValueError if `plan` is not a preview plan, rather than returning
    an empty list -- "I could not find any steps" and "there are no
    destructive steps" must not share an exit code.
    """
```

## Block 4 (source lines 90-96)

```text
    """Read the caller's coverage map: program dir basename -> constructors.

    Raises ValueError for anything but an object of non-empty lists of
    non-empty strings. An empty list is refused, not read as "nothing to
    check": it would let a directory pass `--verify-coverage` having been
    checked for nothing.
    """
```

## Block 5 (source lines 115-119)

```text
    """Match a resource *declaration*, not a mention of the same text.

    A bare substring search reports a false pass: a constructor that has been
    refactored away usually survives elsewhere as a comment or an import.
    """
```

## Block 6 (source lines 124-132)

```text
    """Fail if a directory's expected constructors are no longer declared.

    The quiet way this gate dies: a resource migrates out of the program (into
    a package, into another project) while the plan check keeps passing on
    whatever is left. Nothing errors, and the type the map promised this
    directory declares is no longer anything the repository reviews. Failing
    here forces the map -- and therefore the guard's actual coverage -- to be
    updated in the same change that moves the resource.
    """
```

## Block 7 (source lines 199-205)

```text
    """Run fn with stdout captured, returning its result and what it printed.

    `verify_coverage` emits `::error::`-prefixed lines on the failure fixtures
    by design, and that prefix is a GitHub Actions annotation trigger. Letting
    it through would flag a fully passing self-test run as an error in the
    Actions UI, indistinguishable from a real one.
    """
```
