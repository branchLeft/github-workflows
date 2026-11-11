# docs-lint.test.sh: whitebox span tests

Whitebox: assert on blank_spans' own output, not just a rule's final
verdict. This matters because the final verdict alone cannot distinguish a
genuinely span-aware exclusion from a text-search removal that happens to
self-heal for today's except patterns (every one of which is a literal
prefix of the id family it collides with) -- it would only diverge on a
future except pattern that isn't a prefix, by which point a verdict-only
test suite would already be lying green.

Sourcing with DOCS_LINT_SOURCE_ONLY=1 runs the real docs-lint.sh up through
blank_spans' definition and returns before it touches any file, so this
calls the actual production function, not a reimplementation that could
drift from it.
