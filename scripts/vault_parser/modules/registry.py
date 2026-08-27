"""The ordered module pipeline.

Adding an iteration to this project means writing one module file and adding
one line to `_MODULE_CLASSES`. Nothing in the core changes.

Order matters and is expressed by each module's `order` attribute rather than
by list position or by the filename, so the list below can stay grouped by
theme while the pipeline stays strictly sequenced:

    10 comments -> 20 links -> 30 inline_dataview -> 40 callouts -> 50 cleanup
    -> 60 dataview_queries -> 70 nav_tree -> 80 zoommap -> 82 image_licensing
    -> 85 empty_headings -> 90 graph

Module files are named for what they do, not for where they sit. `order` is the
only thing that sequences them, so it cannot drift out of step with a number in
a filename -- which is exactly what happened under the previous `mNN_` scheme,
where `m08b_empty_headings` (85) sorted ahead of `m08c_image_licensing` (82).

Comments run first because Obsidian removes them before parsing block
structure: a `%%comment%%` line inside a callout must not end the callout, and
a commented-out link must not become a link. Links then run before inline
dataview because dataview can emit a link, and callouts run after both because
they wrap content the earlier modules have already produced.
"""

from __future__ import annotations

from ..config import Config
from ..model import Note, VaultContext
from .base import ModuleStats, TransformModule
from .comments import CommentsModule
from .links import LinksModule
from .inline_dataview import InlineDataviewModule
from .callouts import CalloutsModule
from .cleanup import CleanupModule
from .dataview_queries import DataviewQueriesModule
from .nav_tree import NavTreeModule
from .zoommap import ZoommapModule
from .empty_headings import EmptyHeadingsModule
from .image_licensing import ImageLicensingModule
from .graph import GraphModule
from .i18n_infobox import I18nInfoboxModule
from .lang_blocks import LangBlocksModule

_MODULE_CLASSES: tuple[type[TransformModule], ...] = (
    CommentsModule,
    LangBlocksModule,
    LinksModule,
    InlineDataviewModule,
    CalloutsModule,
    CleanupModule,
    DataviewQueriesModule,
    I18nInfoboxModule,
    NavTreeModule,
    ZoommapModule,
    EmptyHeadingsModule,
    ImageLicensingModule,
    GraphModule,
)


class Pipeline:
    """Instantiates modules from config and runs them in order."""

    def __init__(self, modules: list[TransformModule]) -> None:
        self.modules = sorted(modules, key=lambda m: m.order)

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        only: set[str] | None = None,
        skip: set[str] | None = None,
        also: set[str] | None = None,
    ) -> "Pipeline":
        modules: list[TransformModule] = []

        for module_class in _MODULE_CLASSES:
            setting = config.module(module_class.name)
            enabled = setting.enabled
            if only is not None:
                enabled = module_class.name in only
            # `also` turns a module on *alongside* the configured set, which is
            # what previewing an off-by-default module actually needs: the late
            # ones read markup the earlier modules produce, so running one
            # alone with `--only` shows it a document that never existed.
            if also and module_class.name in also:
                enabled = True
            if skip and module_class.name in skip:
                enabled = False
            modules.append(module_class(enabled=enabled, options=setting.options))

        return cls(modules)

    @property
    def active(self) -> list[TransformModule]:
        return [module for module in self.modules if module.enabled]

    def run_note(self, note: Note, ctx: VaultContext) -> Note:
        """Pass one note through every enabled module, in order.

        A note split by `lang_blocks` carries one body per language, and every
        module runs once per body. Each pass sees a document written in a
        single language, so modules that reason about document structure --
        `empty_headings` above all -- stay correct without knowing i18n
        exists. `note.body` is swapped in and out around each pass, so a module
        reads and writes the one field it always did.

        `lang_blocks` itself is the exception: it is what *creates* the split,
        so it must run once, against the whole document.
        """
        for module in self.active:
            module.stats.notes_seen += 1
            before = note.body

            if module.name == LangBlocksModule.name or not note.lang_bodies:
                note = module.transform(note, ctx)
            else:
                note = self._run_per_language(module, note, ctx)

            if note.body != before:
                module.note_changed()
        return note

    @staticmethod
    def _run_per_language(module: TransformModule, note: Note, ctx: VaultContext) -> Note:
        """Run one module once for each of the note's language bodies."""
        default = ctx.config.i18n.default
        for code in list(note.lang_bodies):
            note.body = note.lang_bodies[code]
            note.active_language = code
            note = module.transform(note, ctx)
            note.lang_bodies[code] = note.body

        note.active_language = default
        # Leave `body` holding the default language, which is what `emit` and
        # every language-unaware reader expects to find there.
        note.body = note.lang_bodies.get(default, note.body)
        return note

    def run_finalizers(self, ctx: VaultContext) -> None:
        for module in self.active:
            module.finalize(ctx)

    def stats(self) -> list[ModuleStats]:
        return [module.stats for module in self.active]

    def describe(self) -> list[tuple[str, bool, bool, str]]:
        """(name, enabled, is_stub, summary) for every registered module."""
        return [
            (module.name, module.enabled, module.stub, module.summary)
            for module in self.modules
        ]

    def unknown_names(self, names: set[str]) -> set[str]:
        known = {module.name for module in self.modules}
        return names - known
