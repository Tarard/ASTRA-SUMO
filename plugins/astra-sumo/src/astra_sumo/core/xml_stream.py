"""Read an XML document one direct child of its root at a time.

A parsed tree of a city network or OSM extract needs many times the file size in memory, so the build stages
walk the direct children of the root instead. Each child is complete when it is handed out, including the
``tail`` text that follows it, so ``ET.tostring(child)`` gives the same bytes as in a fully parsed tree.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from itertools import chain
from pathlib import Path
from types import GeneratorType
from typing import IO, AnyStr
from xml.etree import ElementTree as ET

from .artifact_io import atomic_writer

BLOCK_SIZE = 64 * 1024


def read_blocks(handle: IO[AnyStr], size: int = BLOCK_SIZE) -> Iterator[AnyStr]:
    """Blocks of an open text or binary file, read in the block size ElementTree.parse uses."""
    while block := handle.read(size):
        yield block


def binary_blocks(path: Path, size: int = BLOCK_SIZE) -> Iterator[bytes]:
    """Blocks of a file read as bytes, as ElementTree.parse(path) reads it."""
    with Path(path).open("rb") as handle:
        yield from read_blocks(handle, size)


def text_blocks(path: Path, size: int = BLOCK_SIZE) -> Iterator[str]:
    """Blocks of a file decoded as ``Path.read_text(encoding="utf-8")`` decodes it."""
    with Path(path).open(encoding="utf-8") as handle:
        yield from read_blocks(handle, size)


def _close(blocks: Iterator[object]) -> None:
    """Close a block generator now, so the file it reads is released when parsing ends or fails.

    A parse error raised from the parser keeps itself, and with it the suspended generator still inside its
    ``with open(...)``, alive in a reference cycle, so the file would stay open (and locked on Windows) until the
    cyclic garbage collector runs. Other block sources, such as an open file passed as its own blocks, belong to
    the caller and stay open.
    """
    if isinstance(blocks, GeneratorType):
        blocks.close()


def compact_tree(blocks: Iterable[str | bytes], *, without_params_of: Iterable[str] = ()) -> ET.Element:
    """Parse a whole document into a tree whose equal attribute values, texts and tails are one shared string.

    Sharing equal strings changes nothing a reader can see, but a city network needs about a third of the memory
    of a plain parse. The ``param`` children of elements tagged ``without_params_of`` are left out, about half of
    the elements of an OSM-derived network; pass those tags only for readers that never look at such parameters.
    Parse errors are raised as a whole-document parse raises them.
    """
    drop = frozenset(without_params_of)
    parser = ET.XMLPullParser(("start", "end"))
    shared: dict[str, str] = {}
    open_elements: list[ET.Element] = []
    root: ET.Element | None = None
    source = iter(blocks)
    try:
        for block in chain(source, [None]):
            if block is None:
                parser.close()
            else:
                parser.feed(block)
            for event, element in parser.read_events():
                if event == "start":
                    if root is None:
                        root = element
                    open_elements.append(element)
                    continue
                open_elements.pop()
                attributes = element.attrib
                for key, value in attributes.items():
                    attributes[key] = shared.setdefault(value, value)
                if element.text is not None:
                    element.text = shared.setdefault(element.text, element.text)
                # Each child's tail is complete once its parent ends.
                for child in element:
                    if child.tail is not None:
                        child.tail = shared.setdefault(child.tail, child.tail)
                if element.tag == "param" and open_elements and open_elements[-1].tag in drop:
                    open_elements[-1].remove(element)
    finally:
        _close(source)
    return root  # parser.close() raises for a document without a root element


class DirectChildren:
    """Iterate over the direct children of the document root in document order.

    A child is handed out when the next sibling starts or the root ends, then removed from the root. The root
    keeps its tag, attributes and text and is available as ``root`` once iteration has started. Namespace
    declarations are collected in ``namespaces`` when ``record_namespaces`` is set. Parse errors are raised as
    ``ET.ParseError`` exactly as a whole-document parse raises them.
    """

    def __init__(self, blocks: Iterable[str | bytes], *, record_namespaces: bool = False) -> None:
        self._blocks = blocks
        self._events = ("start", "end", "start-ns") if record_namespaces else ("start", "end")
        self.root: ET.Element | None = None
        self.namespaces: list[tuple[str, str]] = []

    def __iter__(self) -> Iterator[ET.Element]:
        parser = ET.XMLPullParser(self._events)
        previous, depth = None, 0
        source = iter(self._blocks)
        try:
            for block in chain(source, [None]):
                if block is None:
                    parser.close()
                else:
                    parser.feed(block)
                for event, element in parser.read_events():
                    if event == "start-ns":
                        self.namespaces.append(element)
                    elif event == "start":
                        if self.root is None:
                            self.root = element
                            continue
                        if depth == 0 and previous is not None:
                            # The new sibling is already attached, so the finished child is the first one.
                            yield previous
                            previous = None
                            del self.root[0]
                        depth += 1
                    elif element is not self.root:
                        depth -= 1
                        if depth == 0:
                            previous = element
        finally:
            _close(source)
        if previous is not None:
            yield previous
            del self.root[0]


class _NamespacedElement(Exception):
    """A top-level element uses a namespace, which ElementTree declares on the root of the whole tree."""


def _uses_namespace(element: ET.Element) -> bool:
    return any(name[:1] == "{" for node in element.iter() for name in (node.tag, *node.keys()))


def rewrite_indented(
    path: Path,
    edit: Callable[[int, ET.Element], ET.Element | None],
    *,
    space: str = "    ",
) -> None:
    """Rewrite ``path`` as ``ET.indent(root, space)`` and ``ElementTree.write(path, "utf-8", xml_declaration=True)``
    write the edited tree, one top-level element at a time.

    ``edit(index, element)`` receives each top-level element with its position in the file and returns the element
    to keep (changed in place or a replacement) or ``None`` to drop it. The file is written in text mode as
    ElementTree writes it, so line ends follow the platform, and replaced only once complete. A tree whose
    top-level elements use namespaces, or that keeps no element, is rewritten as one tree.
    """
    path = Path(path)
    level_indent = "\n" + space
    try:
        with (
            atomic_writer(path, mode="w", encoding="utf-8", errors="xmlcharrefreplace") as handle,
            path.open("rb") as original,
        ):
            stream = DirectChildren(read_blocks(original))
            previous: ET.Element | None = None
            end_tag = ""

            def write(child: ET.Element, *, last: bool) -> None:
                ET.indent(child, space=space, level=1)
                if not child.tail or not child.tail.strip():
                    child.tail = level_indent
                if last and not child.tail.strip():
                    child.tail = "\n"
                handle.write(ET.tostring(child, encoding="unicode"))

            for index, element in enumerate(stream):
                kept = edit(index, element)
                if kept is None:
                    continue
                if _uses_namespace(kept):
                    raise _NamespacedElement
                if previous is None:
                    root = stream.root
                    if root.tag[:1] == "{":
                        raise _NamespacedElement
                    head = ET.Element(root.tag, root.attrib)
                    head.text = root.text if root.text and root.text.strip() else level_indent
                    serialized = ET.tostring(head, encoding="unicode", short_empty_elements=False)
                    split = serialized.rindex("</")
                    end_tag = serialized[split:]
                    handle.write("<?xml version='1.0' encoding='utf-8'?>\n" + serialized[:split])
                else:
                    write(previous, last=False)
                previous = kept
            if previous is None:
                raise _NamespacedElement
            write(previous, last=True)
            handle.write(end_tag)
    except _NamespacedElement:
        tree = ET.parse(path)
        root = tree.getroot()
        for index, element in enumerate(list(root)):
            kept = edit(index, element)
            if kept is None:
                root.remove(element)
            elif kept is not element:
                root[list(root).index(element)] = kept
        ET.indent(root, space=space)
        tree.write(path, encoding="utf-8", xml_declaration=True)
