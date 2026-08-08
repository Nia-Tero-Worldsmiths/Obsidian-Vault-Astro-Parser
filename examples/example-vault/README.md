# Example vault

A four-note vault that exists so the parser can be run without a real one.
Every feature in it is there because some module needs a case to prove itself
against, not because the world needs describing.

```bash
cp vault.config.example.yaml vault.config.yaml
```

Then set `vault_root` to this folder:

```yaml
vault_root: examples/example-vault
```

and run the parser from the project root:

```bash
.venv/Scripts/python -m scripts.vault_parser --dry-run --verbose
.venv/Scripts/python -m scripts.vault_parser
npm run dev
```

The collection names in `vault.config.example.yaml` already match the folders
here, so `vault_root` is the only line that needs changing.

## What each file is for

| file | exercises |
|---|---|
| `World Atlas/Puerto Ejemplo.md` | infobox callout, resolved and unresolved links, a dataview block, an inline query, a stripped comment, an empty heading |
| `World Encyclopedia/Gremio de Ejemplo.md` | a table with column alignment, tags, an embed that provenance blocks |
| `Prosa/1 - Fragmento.md` | the `prosa` cssclass, a horizontal rule, a note with no outgoing links |
| `_Borrador privado.md` | never emitted: `ignore_globs` drops it before the publish gate is consulted |
| `z_Assets/CREDITS.yaml` | one image published, one blocked |

## What is not here

No `.obsidian/` directory, so **`extract_theme.py` will not run against this
vault**. It needs the ITS Theme and the Cards snippet, which are third-party
and not ours to redistribute — bundling them would put an 846 KB GPL-2.0 file
in a repository that otherwise carries none.

The site therefore builds unstyled from this vault. That is expected: the
example proves the *parser*, and the theme extractor is documented separately
in `scripts/README.md`.
