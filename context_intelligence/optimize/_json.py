"""Private bounded JSON span index for the CI-owned capture adapter.

Not an extensible transform API. Strings and scalar spellings remain in the
source; the disk index retains only offsets and small navigation labels.
"""

from __future__ import annotations

import codecs
import json
import re
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, BinaryIO

CHUNK = 65536
LABEL = 4096
_SPECIAL = re.compile(rb'["\\\x00-\x1f]')
_NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


class FormatError(ValueError):
    """Sanitized refusal: no prompt, token or source text in diagnostics."""


class Reader:
    def __init__(self, source: BinaryIO, poll: Callable[[], None] | None = None):
        self.source = source
        self.poll = poll
        self.block = b""
        self.at = 0
        self.offset = source.tell()
        self.line = 1
        self.line_start = self.offset

    def peek(self) -> bytes:
        if self.at == len(self.block):
            if self.poll:
                self.poll()
            self.block = self.source.read(CHUNK)
            self.at = 0
        return self.block[self.at : self.at + 1]

    def take(self, count: int = 1) -> bytes:
        result = bytearray()
        while count and self.peek():
            size = min(count, len(self.block) - self.at)
            piece = self.block[self.at : self.at + size]
            result.extend(piece)
            newlines = piece.count(b"\n")
            if newlines:
                self.line += newlines
                self.line_start = self.offset + piece.rfind(b"\n") + 1
            self.at += size
            self.offset += size
            count -= size
        if count:
            raise FormatError("incomplete JSON token")
        return bytes(result)

    def whitespace(self) -> bool:
        newline = False
        while (value := self.peek()) and value in b" \t\r\n":
            newline |= value == b"\n"
            self.take()
        return newline


def string_chunks(reader: Reader) -> Iterator[bytes]:
    """Decode one JSON string incrementally, including escaped surrogate pairs."""
    if reader.take() != b'"':
        raise FormatError("expected JSON string")
    utf8 = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        while reader.peek():
            match = _SPECIAL.search(reader.block, reader.at)
            end = match.start() if match else len(reader.block)
            if end > reader.at:
                raw = reader.take(end - reader.at)
                utf8.decode(raw, final=False)
                yield raw
            if not match:
                continue
            token = reader.take()
            if token == b'"':
                utf8.decode(b"", final=True)
                return
            utf8.decode(b"", final=True)
            utf8.reset()
            if token != b"\\":
                raise FormatError("control character in JSON string")
            escape = reader.take()
            if escape == b"u":
                digits = reader.take(4)
                if not re.fullmatch(rb"[0-9a-fA-F]{4}", digits):
                    raise FormatError("invalid JSON unicode escape")
                point = int(digits, 16)
                if 0xD800 <= point <= 0xDBFF:
                    if reader.take(2) != b"\\u":
                        raise FormatError("unpaired JSON surrogate")
                    low_digits = reader.take(4)
                    if not re.fullmatch(rb"[0-9a-fA-F]{4}", low_digits):
                        raise FormatError("invalid JSON unicode escape")
                    low = int(low_digits, 16)
                    if not 0xDC00 <= low <= 0xDFFF:
                        raise FormatError("unpaired JSON surrogate")
                    point = 0x10000 + ((point - 0xD800) << 10) + low - 0xDC00
                if 0xDC00 <= point <= 0xDFFF:
                    raise FormatError("unpaired JSON surrogate")
                yield chr(point).encode("utf-8")
            else:
                decoded = {
                    b'"': b'"',
                    b"\\": b"\\",
                    b"/": b"/",
                    b"b": b"\b",
                    b"f": b"\f",
                    b"n": b"\n",
                    b"r": b"\r",
                    b"t": b"\t",
                }.get(escape)
                if decoded is None:
                    raise FormatError("invalid JSON escape")
                yield decoded
    except UnicodeError as exc:
        raise FormatError("invalid JSON unicode") from exc
    raise FormatError("unterminated JSON string")


class Index:
    """Disk-backed structure for a single explicitly selected CI capture."""

    def __init__(self, source: BinaryIO, poll: Callable[[], None] | None = None):
        self.source = source
        self.reader = Reader(source, poll)
        self.directory = tempfile.TemporaryDirectory(prefix="ci-optimize-index-")
        self.db = sqlite3.connect(Path(self.directory.name) / "spans.sqlite3")
        self.db.execute("PRAGMA cache_size=-1024")
        self.db.execute(
            "CREATE TABLE node(id INTEGER PRIMARY KEY,parent INTEGER,key TEXT,"
            "kind TEXT,start INTEGER,end INTEGER,small TEXT,member_start INTEGER,UNIQUE(parent,key))"
        )
        self.db.execute(
            "CREATE TABLE record(ordinal INTEGER PRIMARY KEY,node INTEGER,start INTEGER,end INTEGER)"
        )
        self.records = 0
        try:
            self.reader.whitespace()
            while self.reader.peek():
                line = self.reader.line
                start = self.reader.line_start
                node = self._value(None, None, 0)
                if self.kind(node) != "object":
                    raise FormatError("capture record must be an object")
                if line != self.reader.line:
                    raise FormatError("multiline JSON is not a supported CI JSONL record")
                while (token := self.reader.peek()) and token in b" \t\r":
                    self.reader.take()
                if self.reader.peek() and self.reader.take() != b"\n":
                    raise FormatError("capture records require a newline separator")
                self.records += 1
                self.db.execute(
                    "INSERT INTO record VALUES(?,?,?,?)",
                    (self.records, node, start, self.reader.offset),
                )
                self.reader.whitespace()
            self.db.commit()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> Index:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()
        self.directory.cleanup()

    def _label(self) -> str | None:
        value = bytearray()
        oversized = False
        for part in string_chunks(self.reader):
            if len(value) + len(part) > LABEL:
                oversized = True
            if not oversized:
                value.extend(part)
        return None if oversized else value.decode("utf-8")

    def _value(
        self, parent: int | None, key: str | None, depth: int, member_start: int | None = None
    ) -> int:
        if depth > 64:
            raise FormatError("JSON nesting exceeds supported capture depth")
        self.reader.whitespace()
        start = self.reader.offset
        token = self.reader.peek()
        kind = {b"{": "object", b"[": "array", b'"': "string"}.get(token, "scalar")
        try:
            cursor = self.db.execute(
                "INSERT INTO node(parent,key,kind,start,member_start) VALUES(?,?,?,?,?)",
                (parent, key, kind, start, member_start),
            )
        except sqlite3.IntegrityError as exc:
            raise FormatError("duplicate JSON object key") from exc
        assert cursor.lastrowid is not None
        node = cursor.lastrowid
        small = None
        if kind in ("object", "array"):
            self.reader.take()
            ending = b"}" if kind == "object" else b"]"
            self.reader.whitespace()
            ordinal = 0
            if self.reader.peek() != ending:
                while True:
                    member = self.reader.offset
                    if kind == "object":
                        label = self._label()
                        if label is None:
                            raise FormatError("oversized JSON key; file protected")
                        self.reader.whitespace()
                        if self.reader.take() != b":":
                            raise FormatError("expected JSON colon")
                    else:
                        label = str(ordinal)
                    self._value(node, label, depth + 1, member)
                    ordinal += 1
                    self.reader.whitespace()
                    if self.reader.peek() == ending:
                        break
                    if self.reader.take() != b",":
                        raise FormatError("expected JSON delimiter")
                    self.reader.whitespace()
            if self.reader.take() != ending:
                raise FormatError("incomplete JSON container")
        elif kind == "string":
            small = self._label()
        else:
            raw = bytearray()
            while (token := self.reader.peek()) and token not in b" \t\r\n,]}":
                if len(raw) >= LABEL:
                    raise FormatError("oversized JSON scalar; file protected")
                raw.extend(self.reader.take())
            if raw not in (b"true", b"false", b"null") and not _NUMBER.fullmatch(raw):
                raise FormatError("invalid JSON scalar")
            small = raw.decode("ascii")
        self.db.execute(
            "UPDATE node SET end=?,small=? WHERE id=?", (self.reader.offset, small, node)
        )
        return node

    def child(self, node: int | None, key: str) -> int | None:
        row = self.db.execute(
            "SELECT id FROM node WHERE parent=? AND key=?", (node, key)
        ).fetchone()
        return row[0] if row else None

    def children(self, node: int | None) -> Iterator[int]:
        for row in self.db.execute("SELECT id FROM node WHERE parent=? ORDER BY start", (node,)):
            yield row[0]

    def kind(self, node: int | None) -> str | None:
        row = self.db.execute("SELECT kind FROM node WHERE id=?", (node,)).fetchone()
        return row[0] if row else None

    def small(self, node: int | None) -> str | None:
        row = self.db.execute("SELECT small FROM node WHERE id=?", (node,)).fetchone()
        return row[0] if row else None

    def span(self, node: int) -> tuple[int, int]:
        return self.db.execute("SELECT start,end FROM node WHERE id=?", (node,)).fetchone()

    def bounded_value(self, node: int | None, limit: int = LABEL) -> Any:
        if node is None:
            return None
        start, end = self.span(node)
        if end - start > limit:
            raise FormatError("oversized projection field; file protected")
        self.source.seek(start)
        return json.loads(self.source.read(end - start))

    def decoded(self, node: int) -> Iterator[bytes]:
        if self.kind(node) != "string":
            raise FormatError("expected encoded image string")
        self.source.seek(self.span(node)[0])
        yield from string_chunks(Reader(self.source, self.reader.poll))

    def roots(self) -> Iterator[tuple[int, int]]:
        yield from self.db.execute("SELECT ordinal,node FROM record ORDER BY ordinal")

    def record_span(self, ordinal: int) -> tuple[int, int]:
        return self.db.execute(
            "SELECT start,end FROM record WHERE ordinal=?", (ordinal,)
        ).fetchone()
