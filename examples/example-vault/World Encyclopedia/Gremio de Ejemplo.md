---
NoteType: organizacion
publish: true
# The same classes the real party notes carry: Cards.css turns the query
# table's `tbody` into a grid and every `tr` into a portrait card. A section
# label has to be a <caption> precisely because of this -- a header row would
# draw as one more card.
cssclasses:
  - cards
  - cards-cols-2
tags:
  - Organización
---

> [!infobox]
> # Gremio de Ejemplo
>
> || Info. narrativa |
> | ----------- | ----------- |
> | Sede | [[Puerto Ejemplo]] |
> | Tipo | Gremio mercante |

A guild, linked back to [[Puerto Ejemplo]] so the graph module has an edge to
draw and the navigation tree has something to nest.

## Miembros

El bloque de abajo reemplaza una consulta ```dataview. Va vacío: el diseño sale
del `NoteType` de esta nota y el término de búsqueda, de su propio nombre.

Está por encima del primer marcador `:::lang`, así que es contenido compartido:
se muestra en todos los idiomas, con las cabeceras y los nombres de cada
miembro en el que el lector tenga activo. Esa es la colocación recomendada. La
otra es dentro de un bloque concreto, para una tabla que deba ir en mitad de
una estructura que solo existe en ese idioma.

```i18n-query
```

:::lang es
Y esta tabla escrita a mano sigue aquí para el resto de módulos:

| Nombre | Rango | Notas |
| ------ | ----- | ----- |
| Ana | Maestra | Alignment syntax below is deliberate |
| Beto | Oficial | The renderer emits inline `text-align` |
| Cira | Aprendiz | which is why `base.css` has no `[align]` rules |
:::

:::lang ja
手書きの表はスペイン語の節にだけあります。上の名簿は両方の言語に出ます。
:::

:::lang es
## Notas

The image below is marked `origin: external` in `z_Assets/CREDITS.yaml`, so the
`image_licensing` module replaces it with a placeholder and never copies the
file. Enable that module and the gap appears; disable it and the image is
published. It is the smallest end-to-end proof that the provenance file is
actually load-bearing.

![[retrato-ajeno.svg]]

Tags render as pills: #Organización

**Bold**, *italic* and ~~struck~~ text, so the base stylesheet has something to
apply to.
:::
