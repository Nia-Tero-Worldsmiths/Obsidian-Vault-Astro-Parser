---
NoteType: persona
title: Persona de Ejemplo
title_ja: 見本の人物
title_en: Example Person
publish: true
estado: Vivo
imagen:
  - retrato-generico.svg
  - retrato-alterno.svg
# Zero-based. Which image stands for this note: the one the infobox
# opens on, and the one a roster row shows. Absent means the first.
defaultImageIndex: 1
imageLabel:
  - Retrato
  - Forma alterna (etiqueta larga a propósito)
# Membership without a tag -- the case a tag query cannot see. The group box
# finds this note because `organizacion` names the guild.
organizacion: "[[Gremio de Ejemplo]]"
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
