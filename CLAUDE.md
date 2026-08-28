# proto-specs

Open-source **specifications** for prototype apps built in private repos — precise
enough that a coding agent with no access to the original code can rebuild them.
The repo root is the live site, served on two hosts from the same commit:
<https://proto-specs.vercel.app/> and <https://eswat2.github.io/proto-specs/>.
Neither host runs a build step — a push updates both.

## Commands

```sh
python3 build.py           # validate + regenerate index.html
python3 build.py --check   # validate, and exit 2 if index.html is stale
```

Only dependency is PyYAML (`python3 -m pip install pyyaml`). There is no test
suite, no linter, and no dev server — open `index.html` with `file://` or serve
the directory.

`build.py` validates before it writes and returns non-zero **without touching
`index.html`** if the YAML is bad. It checks required fields, duplicate slugs,
link entries missing a label or href, prose links whose target is neither
absolute nor a path, and — for a project relying on the derived links — that
`{slug}/SPEC.md` and `{slug}/architecture.html` actually exist. Fix the YAML
rather than working around a failure.

## `index.html` is generated and tracked

It is committed because both hosts serve the repo root with no build step, so the
built page has to be in the commit. **Never hand-edit it.** Content changes go in
`data/site.yaml`, markup and CSS changes go in `template.html`, then rerun the
build. Every content change therefore produces a two-file diff; that is expected.

## Architecture

**Python is the only renderer.** `build.py` produces every byte of `index.html`;
the page ships finished and does no rendering of its own. There is no JSON
payload in the HTML and no client-side JavaScript.

`template.html` is a **complete, valid HTML document** with `__TOKEN__` holes in
it — open it directly and you get the real chrome and styling with the token
names showing where the content goes, which makes CSS work checkable without a
build. `build.py` fills the holes (`__DOCTITLE__`, `__DESC__`, `__KICKER__`,
`__TITLE__`, `__DEK__`, `__PROJECTS__`, `__FOOT_META__` and the `__AUTHOR_*__`
set) and writes `index.html`. That is the whole build. A token named in
`render_page()` but missing from the template is a hard error, so a renamed hole
fails loudly instead of silently dropping content.

### Prose fields carry mini-markdown, not HTML

`meta.dek`, `footer.meta` and `footer.author.text` accept exactly three inline
forms — `**bold**`, `` `code` ``, `[text](url)`. `rich()` escapes the string
first and expands those three afterwards, so the expansion is the only thing that
can produce a tag and a stray `<` in the YAML stays a literal `<` on the page.
Every other field is escaped only. Do not put raw HTML in the YAML.

### Project links are derived from the slug

A project with `slug: poker` gets `https://github.com/{meta.repo}/blob/main/poker/SPEC.md`
and `poker/architecture.html` — the two links every card carries today, which is
why they cannot drift apart. A project that does not fit that shape carries an
explicit `links: [{label, href}, ...]` instead; the slug stays required either
way because it is the card's identity. Author-card tags derive the same way from
`footer.author.base`.

## Favicon and paths

`favicon.svg` is the source and `favicon.ico` is the 32px raster beside it.
**`build.py` does not produce either** — if the SVG changes, re-render the `.ico`
by hand or the two silently drift.

The favicon links live in `template.html` and their hrefs are **relative**
(`href="favicon.ico"`) on purpose. The two hosts put the repo root at different
depths — `/` on Vercel, `/proto-specs/` on Pages — so an absolute `/favicon.ico`
would escape the project on Pages and land on the personal user site at
`eswat2.github.io`. Do not "tidy" them into absolute paths. The same reasoning
applies to the derived `{slug}/architecture.html` project links.

## Hand-authored, not generated

`poker/architecture.html` and `retro/architecture.html` are written by hand and
are not part of the build. So are the two `SPEC.md` files — those are the point
of the repo.
