#!/usr/bin/env python3
"""Build the proto-specs landing page from data/site.yaml.

Output (generated -- do not hand-edit):
  index.html     the page, served from the repo root by Vercel and GitHub Pages

Usage:  python3 build.py           validate + regenerate index.html
        python3 build.py --check   validate, and exit 2 if index.html is stale

Exit codes: 0 fine, 1 the YAML is invalid, 2 index.html is out of date (--check).
Only dependency is PyYAML.
"""

from __future__ import annotations

import pathlib
import re
import sys

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required:  python3 -m pip install pyyaml")

HERE = pathlib.Path(__file__).parent
DATA = HERE / "data" / "site.yaml"
TEMPLATE = HERE / "template.html"
INDEX = HERE / "index.html"

META_KEYS = ("repo", "kicker", "title", "description", "dek")

# The three inline forms the prose fields accept, applied to already-escaped
# text so the expansion is the only thing on the page that can produce a tag.
BOLD = re.compile(r"\*\*(.+?)\*\*")
CODE = re.compile(r"`(.+?)`")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


# ---------------------------------------------------------------- rendering
def esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#39;")
    )


def rich(text: str) -> str:
    """Escape, then expand **bold**, `code` and [text](url). Nothing else."""
    out = esc(text)
    out = BOLD.sub(r"<strong>\1</strong>", out)
    out = CODE.sub(r"<code>\1</code>", out)
    out = LINK.sub(r'<a href="\2">\1</a>', out)
    return out


def links_for(project: dict, repo: str) -> list[tuple[str, str]]:
    """The card's links: explicit if given, otherwise derived from the slug."""
    if project.get("links"):
        return [(link["label"], link["href"]) for link in project["links"]]
    slug = project["slug"]
    return [
        ("SPEC.md", f"https://github.com/{repo}/blob/main/{slug}/SPEC.md"),
        ("architecture", f"{slug}/architecture.html"),
    ]


def render_projects(data: dict) -> str:
    repo = data["meta"]["repo"]
    cards = []
    for project in data["projects"]:
        anchors = "\n".join(
            f'        <a href="{esc(href)}">{esc(label)}</a>'
            for label, href in links_for(project, repo)
        )
        cards.append(
            '    <article class="project">\n'
            f'      <h2>{esc(project["title"])}</h2>\n'
            f'      <p>{esc(project["blurb"])}</p>\n'
            '      <div class="links">\n'
            f"{anchors}\n"
            "      </div>\n"
            "    </article>"
        )
    return "\n\n".join(cards)


def render_author_tags(author: dict) -> str:
    base = author["base"].rstrip("/")
    return "\n".join(
        f'        <a href="{esc(f"{base}/{tag}")}">{esc(tag)}</a>'
        for tag in author["tags"]
    )


def render_page(data: dict) -> str:
    meta, footer = data["meta"], data["footer"]
    author = footer["author"]
    body = TEMPLATE.read_text()
    swaps = {
        # The tab title. Optional in the YAML: absent, it is the kicker, so the
        # page and the tab cannot drift apart.
        "__DOCTITLE__": esc(meta.get("doc_title") or meta["kicker"]),
        "__DESC__": esc(meta["description"]),
        "__KICKER__": esc(meta["kicker"]),
        "__TITLE__": esc(meta["title"]),
        "__DEK__": rich(meta["dek"]),
        "__PROJECTS__": render_projects(data),
        "__FOOT_META__": rich(footer["meta"]),
        "__AUTHOR_ARIA__": esc(author["aria"]),
        "__AUTHOR_LABEL__": esc(author["label"]),
        "__AUTHOR_TEXT__": rich(author["text"]),
        "__AUTHOR_TAGS__": render_author_tags(author),
    }
    for token, value in swaps.items():
        if token not in body:
            raise SystemExit(f"template.html is missing the {token} token")
        body = body.replace(token, value)
    return body


# --------------------------------------------------------------- validation
def check_prose(where: str, text: str, errors: list[str]) -> None:
    """Catch link targets that would render as a broken href."""
    for label, href in LINK.findall(text):
        if not label.strip():
            errors.append(f"{where}: link with an empty label")
        if href.startswith(("http://", "https://", "/", "#")) or re.match(r"^[\w.-]+/", href):
            continue
        errors.append(f"{where}: link target {href!r} is neither absolute nor a path")


def check(data: dict) -> list[str]:
    """Catch the mistakes that are easy to make while hand-editing the YAML."""
    errors: list[str] = []

    meta = data.get("meta") or {}
    for key in META_KEYS:
        if not str(meta.get(key) or "").strip():
            errors.append(f"meta: {key} is required")
    check_prose("meta.dek", str(meta.get("dek") or ""), errors)

    projects = data.get("projects") or []
    if not projects:
        errors.append("projects: at least one project is required")

    seen: set[str] = set()
    for project in projects:
        where = project.get("slug") or project.get("title", "<untitled>")
        for key in ("slug", "title", "blurb"):
            if not str(project.get(key) or "").strip():
                errors.append(f"{where}: {key} is required")

        slug = project.get("slug")
        if slug:
            if slug in seen:
                errors.append(f"{where}: duplicate slug")
            seen.add(slug)

        if project.get("links"):
            for link in project["links"]:
                if not link.get("label") or not link.get("href"):
                    errors.append(f"{where}: every link needs a label and an href")
        elif slug:
            # The derived pair only makes sense if the two files are really there.
            for name in ("SPEC.md", "architecture.html"):
                if not (HERE / slug / name).exists():
                    errors.append(f"{where}: {slug}/{name} does not exist")

    footer = data.get("footer") or {}
    if not str(footer.get("meta") or "").strip():
        errors.append("footer: meta is required")
    check_prose("footer.meta", str(footer.get("meta") or ""), errors)

    author = footer.get("author") or {}
    for key in ("aria", "label", "text", "base"):
        if not str(author.get(key) or "").strip():
            errors.append(f"footer.author: {key} is required")
    check_prose("footer.author.text", str(author.get("text") or ""), errors)
    if not author.get("tags"):
        errors.append("footer.author: at least one tag is required")

    return errors


# --------------------------------------------------------------------- main
def main(argv: list[str]) -> int:
    check_only = "--check" in argv[1:]
    unknown = [arg for arg in argv[1:] if arg != "--check"]
    if unknown:
        sys.exit(f"unknown argument: {unknown[0]}  (usage: build.py [--check])")

    data = yaml.safe_load(DATA.read_text())
    errors = check(data)
    if errors:
        print(f"{DATA.name} is invalid:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    page = render_page(data)

    if check_only:
        current = INDEX.read_text() if INDEX.exists() else None
        if current != page:
            print("index.html is out of date -- run: python3 build.py", file=sys.stderr)
            return 2
        print("index.html is up to date")
        return 0

    INDEX.write_text(page)
    print(f"wrote {INDEX.name}  ({len(data['projects'])} projects)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
