---
publish: true
---

# Borrador privado

This note is never emitted, even though its frontmatter says `publish: true`.

The filename begins with an underscore, and `ignore_globs: ["_*"]` in the
config drops it before the publish gate is ever consulted. It is here so that
the two mechanisms -- the glob and the gate -- can be seen not to be the same
thing.
