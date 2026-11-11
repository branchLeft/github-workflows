# test_attest_npm_tarball_action.py

The program the action runs is tested in ../verify-package-attestations. These run the
action's own `run:` bodies, cut out of action.yml, under bash with a stub `gh` first on
PATH, so what is asserted is the step CI runs and not a copy of it. The attest step itself
is `actions/attest-build-provenance`; it cannot run here, and the structure tests pin how
it is used.

The Sabotage class breaks each control in a copy of the two directories and requires the
named tests to go red there; the unmodified copy must be green.
