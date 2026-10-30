"""Read an XML document one direct child of its root at a time.

A parsed tree of a city network or OSM extract needs many times the file size in memory, so the build stages
walk the direct children of the root instead. Each child is complete when it is handed out, including the
``tail`` text that follows it, so ``ET.tostring(child)`` gives the same bytes as in a fully parsed tree.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from itertools import chain
from pathlib import Path
from typing import IO, AnyStr
from xml.etree import ElementTree as ET

BLOCK_SIZE = 64 * 1024


def read_blocks(handle: IO[AnyStr], size: int = BLOCK_SIZE) -> Iterator[AnyStr]:
    """Blocks of an open text or binary file, read in the block size ElementTree.parse uses."""
    while block := handle.read(size):
        yield block


def binary_blocks(path: Path, size: int = BLOCK_SIZE) -> Iterator[bytes]:
    """Blocks of a file read as bytes, as ElementTree.parse(path) reads it."""
    with Path(path).open("rb") as handle:
        yield from read_blocks(handle, size)


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
        for block in chain(self._blocks, [None]):
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
        if previous is not None:
            yield previous
            del self.root[0]
