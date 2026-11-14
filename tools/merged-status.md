# merged-status.py

Moves a board item to a terminal-but-not-closed status once every pull request
naming its issue has stopped being open.

A merge is not a completion. One issue routinely spans several pull requests
in several repositories, so the merge that fires this is evidence about one
edge and nothing more: the question has to be re-asked of every pull request
naming the issue, and the write happens only when none of them is still open.
Acting on the triggering pull request alone would mark work finished while a
sibling branch is still unreviewed.

## The delivery seam

Nothing in a trailer distinguishes a pull request that DELIVERED an issue
from one that merely NAMED it. The closing keywords GitHub acts on and the
non-closing `Refs` spelling parse to the same edge -- and the keyword cannot
be used to tell them apart in either direction, because a repo is free to
adopt the opposite convention from the obvious one. A repo that delivers some
paths by hand, for instance, has every reason to require the NON-closing
spelling on exactly the pull requests that did the delivering, so that the
merge cannot close an issue whose work is not yet live; there, keying on a
closing keyword writes nothing, ever.

Two knobs express the rule instead, and they are the only place this question
is answered:

  --link-kinds     which trailer spellings create an edge at all
  --from-statuses  which board statuses an item may be moved OUT of

The second carries the discrimination the first cannot: an unstarted item
sitting in a backlog column is not completed by a merge that mentioned it in
passing, whatever keyword did the mentioning, while an item a team had moved
into a working column plausibly is.

Both are inputs with no default here: an unrecognised or empty value is
refused rather than silently narrowed, because a typo that quietly disabled
the write would look exactly like a board with nothing to write.

## Fail closed, everywhere

Every refusal is loud and nothing is inferred from an absence. An API error
is not "no siblings found", a subject that did not merge is not a subject
with no links, and a status this tool was never told to move out of is left
alone rather than guessed at. The one thing it will never do is write on
evidence it could not gather.
