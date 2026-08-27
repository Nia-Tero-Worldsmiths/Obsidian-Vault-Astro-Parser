---
publish: true
cssclasses:
  - cards
  - cards-cols-2
tags:
  - Territorio
---

> [!infobox]
> # Puerto Ejemplo
> ![[escudo-ejemplo.svg]]
>
> || Info. narrativa |
> | ----------- | ----------- |
> | Región | Costa de Ejemplo |
> | Gobierno | Consejo mercante |
> | Fundación | Año 412 |

Puerto Ejemplo is a harbour town, and the note you are reading is a fixture:
it exists so the parser can be run end to end without a real vault. Every
feature below is here because some module has to be able to prove it works.

A link to a note that exists resolves normally: [[Gremio de Ejemplo]].
A link to one that does not is rendered as unresolved rather than as a dead
link: [[Ciudad Que No Existe]].

## Historia

The heading above has content under it, so the `empty_headings` module leaves
it alone. The one below does not, and is removed when that module is enabled --
which is why it is here.

## Sección vacía

## Habitantes

```dataview
LIST
FROM "World Encyclopedia"
WHERE contains(file.name, "Ejemplo")
```

%% A comment. The `comments` module strips this before anything else parses the note,
   which is why a comment inside a callout does not split the block. %%

Inline queries also resolve: this town is in `= this.file.folder`.
