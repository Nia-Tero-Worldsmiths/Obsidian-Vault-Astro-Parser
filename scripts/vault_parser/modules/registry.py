"""The ordered module pipeline.

Adding an iteration to this project means writing one module file and adding
one line to `_MODULE_CLASSES`. Nothing in the core changes.

Order matters and is expressed by each module's `order` attribute rather than
by list position, so the list below can stay grouped by theme while the
pipeline stays strictly sequenced:

    10 comments -> 20 links -> 30 inline_dataview -> 40 callouts -> 50 cleanup
    -> 60 dataview_queries -> 70 nav_tree -> 80 zoommap -> 90 graph

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
from .m01b_comments import CommentsModule
from .m02_links import LinksModule
from .m03_inline_dataview import InlineDataviewModule
from .m04_callouts import CalloutsModule
from .m05_cleanup import CleanupModule
from .m06_dataview_queries import DataviewQueriesModule
from .m07_nav_tree import NavTreeModule
from .m08_zoommap import ZoommapModule
from .m08b_empty_headings import EmptyHeadingsModule
from .m08c_image_licensing import ImageLicensingModule
from .m09_graph import GraphModule

_MODULE_CLASSES: tuple[type[TransformModule], ...] = (
    CommentsModule,
    LinksModule,
    InlineDataviewModule,
    CalloutsModule,
    CleanupModule,
    DataviewQueriesModule,
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
        """Pass one note through every enabled module, in order."""
        for module in self.active:
            module.stats.notes_seen += 1
            before = note.body
            note = module.transform(note, ctx)
            if note.body != before:
                module.note_changed()
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
