---
NoteType: persona
title: Persona de Ejemplo
title_ja: 見本の人物
title_en: Example Person
publish: true
estado: Vivo
especie: Humano
edad: "34"
titulos:
clase: [Rogue, Maga]
subclase: [Arcane Trickster, Bladesinger]
---

This paragraph sits above the first language marker, so it is shared: every
language sees it. That is where a note's infobox and its images belong.

```i18n-infobox
```

:::lang es
## Historia

Texto en español. Solo visible con el idioma **es** activo.
:::

:::lang ja
## 歴史

日本語のテキスト。**ja** を選んだときだけ表示されます。
:::

:::lang en
## History

English text. Only visible when **en** is active.
:::

:::lang all
A block tagged `all` is shown in every language, for material that does not
need translating -- a map, a diagram, a table of numbers.
:::

The four `%%` comments below are ordinary Obsidian comments, not language
markers. They must behave exactly as they do anywhere else in the vault.

%%### Historia%%
%% lang es %%
%% endlang %%

If this paragraph survives in Spanish only, that is correct: unwrapped prose at
or below the first marker is treated as default-language content.

## Bloque de código

The fence below documents the syntax. It must NOT be parsed as a real block --
if it is, this whole code sample disappears when the reader switches language.

```markdown
:::lang es
Esto está dentro de una valla de código.
:::
```
