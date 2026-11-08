"""Site-wide language settings, for the pages the parser does not write.

Emits `src/generated/site.json`: the languages the site actually serves, with
their labels, and which one is the default.

Every other page learns its languages from the note behind it -- `emit` writes
the codes a note carries into its frontmatter, and `LanguageBar` offers those.
The three site-authored pages (`/`, `/galeria`, `/grafo`) have no note behind
them, so there was nowhere for them to read this from, and `LanguageBar` kept
a hand-written copy of the label table with a comment admitting it had to be
kept in step with `vault.config.yaml` by hand.

That hand-sync is the thing worth removing. `languages` minus `ignore` is a
publishing decision made in one place, and a second copy in a component can
only ever drift one way: offering a reader a language the notes were never
emitted in, which looks like a broken page rather than a stale constant.

Writes `served`, not `codes`. A vault that authors in English while the site
withholds it must not offer an English pill -- the content behind it does not
exist yet. That distinction is exactly what `I18nSettings.served` encodes, so
reading it here keeps the two lists from being conflated a second time.

Depends on nothing but configuration, so it runs last and touches no note.
"""

from __future__ import annotations

from ..config import I18nSettings
from ..model import VaultContext
from .base import TransformModule


class SiteIndexModule(TransformModule):
    name = "site_index"
    order = 95
    summary = "Emit the site's served languages as site.json"
    stub = False

    ARTIFACT = "site.json"

    def finalize(self, ctx: VaultContext) -> None:
        i18n = ctx.config.i18n
        served = i18n.served

        ctx.generated[self.ARTIFACT] = {
            # Named `defaultLanguage` rather than `default`, which is a
            # reserved word in a JavaScript import and an awkward property to
            # destructure on the other side.
            "defaultLanguage": i18n.default,
            "languages": [
                {"code": code, "label": i18n.label_for(code)} for code in served
            ],
        }

        self.count("languages served", len(served))
        self._sections(i18n)

    def _sections(self, i18n: I18nSettings) -> None:
        withheld = [code for code in i18n.codes if i18n.is_ignored(code)]
        if not withheld:
            return
        # Worth stating once per build. A language in `ignore` is invisible on
        # the site by design, and the only place that decision is otherwise
        # visible is the absence of a pill nobody is looking for.
        self.section(
            "Languages authored but not served",
            [
                "These are listed in `i18n.languages` and subtracted by",
                "`i18n.ignore`, so the vault accepts them and the site leaves",
                "them out. No language bar offers them.",
                "",
                *(f"  {code}  ({i18n.label_for(code)})" for code in withheld),
            ],
        )
