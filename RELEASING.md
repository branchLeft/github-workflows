# Releasing

Consumers pin reusable workflows either to an exact version (`@v1.2.3`) or to the
moving major alias (`@v1`, currently used by `website` and `ghost-platform`). The
two tag shapes are protected differently, so they're cut differently.

## Cutting a versioned release (`vX.Y.Z`)

Immutable once pushed: deletion, force-push, and re-pointing are all blocked, and the
tag must carry a valid signature.

```bash
git tag -s vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --verify-tag
```

## Advancing the `v1` alias

Deletion is blocked and the tag must be signed, but `v1` stays movable by design —
consumers rely on it advancing. Re-point it to the new release's commit:

```bash
git tag -sf v1 vX.Y.Z -m "v1"
git push origin v1 --force
```

`git tag -s` / `-sf` use the signing key configured by `git config gpg.format` /
`user.signingkey` (SSH signing here — see `~/.gitconfig`).
