"""Markdown structure helpers shared by the repository navigation and skill tests."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from urllib.parse import unquote

from markdown_it import MarkdownIt  # locked in uv.lock through mcp[cli] -> rich

URL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_PARSER = MarkdownIt("commonmark")

# Gitignored local directories: instructions may name them, but a fresh checkout does not have them.
LOCAL_ONLY_DIRS = {".venv", "outputs"}
MODULE_NAME = re.compile(r"(astra_sumo|torii_sumo)(\.\w+)+")
FILE_NAME = re.compile(r"[\w.-]*[\w-]\.[A-Za-z0-9]+")
ROOT_DOTFILE = re.compile(r"\.[A-Za-z][\w-]*")


def link_targets(text: str) -> list[tuple[int, str]]:
    """Relative link and image destinations as CommonMark reads them, with the first line of their block."""
    env: dict = {}
    found = set()
    for block in _PARSER.parse(text, env):
        for token in block.children or []:
            target = token.attrGet("href") if token.type == "link_open" else token.attrGet("src")
            if token.type in ("link_open", "image") and target is not None:
                found.add((block.map[0] + 1, target))
    for definition in env.get("references", {}).values():
        found.add((definition["map"][0] + 1, definition["href"]))

    targets = []
    for line, target in sorted(found):
        target = unquote(target).split("#", 1)[0]
        if target and not URL_SCHEME.match(target):
            targets.append((line, target))
    return targets


def broken_links(files: list[Path], root: Path, contain: dict[str, Path]) -> list[str]:
    """Links that do not resolve, or that leave the folder `contain` names for a path part."""
    broken = []
    for path in files:
        relative = path.relative_to(root)
        for line, target in link_targets(path.read_text(encoding="utf-8")):
            resolved = (path.parent / target).resolve()
            where = f"{relative.as_posix()}:{line} -> {target}"
            if not resolved.exists():
                broken.append(where)
                continue
            for part, folder in contain.items():
                if part in relative.parts and not resolved.is_relative_to(folder.resolve()):
                    broken.append(f"{where} (leaves {folder.relative_to(root).as_posix()})")
    return broken


def code_spans(text: str) -> list[str]:
    """Inline code spans outside fenced or indented code blocks."""
    return [
        token.content
        for block in _PARSER.parse(text)
        for token in block.children or []
        if token.type == "code_inline"
    ]


def named_repository_paths(text: str) -> list[str]:
    """Code spans that name a repository path, leaving out commands, placeholders and module names."""
    paths = []
    for token in code_spans(text):
        if re.search(r"[\s<>*{}$]", token) or URL_SCHEME.match(token) or MODULE_NAME.fullmatch(token):
            continue
        if re.fullmatch(r"[\d.]+", token) or (token.startswith(".") and "/" not in token and token.count(".") > 1):
            continue  # a version, or a suffix such as `.net.xml`
        if "/" in token or FILE_NAME.fullmatch(token) or ROOT_DOTFILE.fullmatch(token):
            if token.split("/", 1)[0] not in LOCAL_ONLY_DIRS:
                paths.append(token)
    return paths


def missing_named_paths(text: str, root: Path) -> list[str]:
    return sorted(path for path in named_repository_paths(text) if not (root / path).exists())


def routed_references(skill_body: str) -> set[str]:
    """Files in the skill's own references/ that a SKILL.md routes to, as a whole code span or a link."""
    routed = set()
    for target in code_spans(skill_body) + [target for _, target in link_targets(skill_body)]:
        if re.search(r"\s", target) or URL_SCHEME.match(target):
            continue
        target = posixpath.normpath(target)
        if target.startswith("references/") and target.endswith(".md"):
            routed.add(target.removeprefix("references/"))
    return routed
