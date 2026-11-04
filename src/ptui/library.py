"""The two query layers, sorting, and display text.

SPEC keeps these strictly apart and so does this module:

* **scope** — a real papis query, run against the database on submit. What it
  supports depends on the papis backend.
* **narrow** — an in-memory filter over the scoped set. Backend-independent,
  identical on every machine.

Sorting applies to the scoped set and is independent of narrowing.
"""

from __future__ import annotations

import math
import os
import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from doi import find_doi_in_text
from rich.cells import cell_len, set_cell_size

# Papis forks a process pool to build its cache. Inside a TUI that copies the
# whole app and dies on Textual's redirected file descriptors, so ask papis for
# the sequential path (PAPIS_NP is papis's own switch — a user can override it).
os.environ.setdefault("PAPIS_NP", "0")

import papis.bibtex
import papis.config
import papis.database
import papis.document
import papis.exceptions
import papis.id
import papis.strings
from papis.document import Document

ID_KEY = papis.id.ID_KEY_NAME


def doc_id(doc: Document) -> str:
    """The stable identity of a document. Never use the folder path — `papis rename` moves it.

    Args:
        doc: The document to identify.

    Returns:
        Its `papis_id`, else its folder, else the Python id of the object, as text.
    """
    return str(doc.get(ID_KEY, doc.get_main_folder() or id(doc)))


_ALIAS = re.compile(r"(^-?|[\s(]-?)([^\s:()]+):")


def expand_aliases(query: str, aliases: dict[str, str]) -> str:
    """Expand `a:Nauen` to `author:Nauen`, per `[query.aliases]`.

    Also right after a `(`, since papis's own query grammar groups with parentheses, and after the `-`
    that negates a narrow term, so `-a:nauen` excludes.

    Args:
        query: The query as typed.
        aliases: Alias to the key it stands for.

    Returns:
        The query with every known alias expanded.
    """
    return _ALIAS.sub(lambda match: match[1] + aliases[match[2]] if match[2] in aliases else match[0], query)


def mtime(doc: Document) -> int:
    """`info.yaml`'s mtime: the version of everything cached about a document.

    Every write goes through `safewrite`, `place` or papis, and all of them touch
    the file, so a `stat` cannot be forgotten at a new write path the way an
    invalidation call can. 0 when there is no file.

    Args:
        doc: The document to ask about.

    Returns:
        `st_mtime_ns` of its `info.yaml`, or 0.
    """
    try:
        return os.stat(doc.get_info_file()).st_mtime_ns
    except (OSError, TypeError, ValueError):
        return 0


def use(name: str) -> None:
    """Make `name` (a configured library or a folder) papis's current library.

    ptui's own queries pass the name explicitly, but `papis add`, `addto` and
    every `papis.database.get()` resolve the *current* one — switching only
    ptui's copy of the name filed a new document in the library left behind.
    `PAPIS_LIB` too: `get_lib()` re-applies it on every call, so it would win.

    Args:
        name: A configured library's name, or a folder.

    Raises:
        ValueError: When papis has no such library.
    """
    try:
        papis.config.set_lib_from_name(name)
    except papis.exceptions.InvalidLibraryError as error:  # its args are two sentences, printed as a tuple
        raise ValueError(" ".join(str(arg).strip() for arg in error.args)) from error
    os.environ["PAPIS_LIB"] = name


def scope(query: str, library: str | None = None) -> list[Document]:
    """Run a real papis query against the database.

    Args:
        query: The papis query; blank means every document.
        library: The library's name, or `None` for the current one.

    Returns:
        The matching documents, in the order papis gives them.
    """
    database = papis.database.get(library or None)
    return database.query(query) if query.strip() else database.get_all_documents()


# ── narrow ──────────────────────────────────────────────────────────────────


def haystack(doc: Document, fields: list[str]) -> str:
    """Build the text a narrow term is searched in.

    Args:
        doc: The document to read.
        fields: The keys to search.

    Returns:
        The values joined by spaces, casefolded, with what the list shows of them added.
    """
    return shown_too(" ".join(display(doc.get(field, "")) for field in fields)).casefold()


def shown_too(text: str) -> str:
    """Add what the list shows of a text, on a line of its own when that differs.

    So `/bert` finds the `{B}ERT` drawn as `BERT`, and `{b}` still finds what is stored.

    Args:
        text: The text as stored.

    Returns:
        `text`, or `text` and its displayed form separated by a newline.
    """
    shown = strip_latex(text)
    return text if shown == text else f"{text}\n{shown}"


def is_subsequence(needle: str, haystack_text: str) -> bool:
    """Tell whether the characters of `needle` appear in order.

    Args:
        needle: The characters to find.
        haystack_text: The text to search.

    Returns:
        `True` when every character is found after the one before it.
    """
    remaining = iter(haystack_text)
    return all(char in remaining for char in needle)


FUZZY_SPAN = 3
"""How far a fuzzy match may spread, as a multiple of the needle. Plain
subsequence matching is useless on real data — `note` matches
`Locality-Atte(n)ding visi(o)n (T)ransform(e)r` — so the run has to stay tight."""


def fuzzy_match(needle: str, haystack_text: str) -> bool:
    """Subsequence, but only when the matched characters sit close together.

    Scans from every possible start so a late tight run still counts; the needle
    is short and the haystack is one document, so the quadratic worst case never
    shows up in practice.

    Args:
        needle: The characters to find.
        haystack_text: The text to search.

    Returns:
        `True` when the whole needle fits in `FUZZY_SPAN` times its length.
    """
    if not needle:
        return True
    budget = len(needle) * FUZZY_SPAN
    for start in range(len(haystack_text)):
        if haystack_text[start] != needle[0]:
            continue
        index = 0
        for offset in range(start, min(len(haystack_text), start + budget)):
            if haystack_text[offset] == needle[index]:
                index += 1
                if index == len(needle):
                    return True
    return False


_FIELD = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):(?!/)(.*)$")
_NUM = r"(\d+(?:\.\d+)?)?"
_RANGE = re.compile(rf"^(>=|<=|>|<)?{_NUM}(\.\.)?{_NUM}$")


@dataclass(frozen=True, slots=True)
class Term:
    """One whitespace-separated word of a narrow query.

    Attributes:
        text: The word, casefolded.
        field: The key it is qualified with; empty means any of the configured narrow fields.
        negate: The term excludes what it matches.
    """

    text: str
    field: str = ""
    """Empty means "any of the configured narrow fields"."""
    negate: bool = False


def parse_query(query: str, aliases: dict[str, str] | None = None) -> tuple[Term, ...]:
    """`-survey a:nauen "vision transformer"` -> three terms, ANDed by the caller.

    Quotes come from `shlex`, which raises on the unbalanced quote you always
    have halfway through typing one — so an unterminated query is retried closed.
    Only `"` quotes: an apostrophe is a letter in `don't` and `o'brien`.

    Args:
        query: The query as typed.
        aliases: Alias to the key it stands for, or `None`.

    Returns:
        The terms, in the order typed.
    """
    query = expand_aliases(query, aliases or {})
    tokens: list[str] = query.split()
    for candidate in (query, f'{query}"'):
        try:
            lexer = shlex.shlex(candidate, posix=True)
            lexer.whitespace_split, lexer.quotes, lexer.commenters = True, '"', ""
            tokens = list(lexer)
            break
        except ValueError:
            continue

    terms = []
    for token in tokens:
        negate = token.startswith("-") and len(token) > 1
        token = token[1:] if negate else token
        # `http://x` must not read as the field `http`, hence the `(?!/)`
        found = _FIELD.match(token)
        field, text = (found.group(1), found.group(2)) if found else ("", token)
        if text:
            terms.append(Term(text.casefold(), field, negate))
    return tuple(terms)


def _in_range(value: str, spec: str) -> bool | None:
    """Test a value against a range like `year:>2023`, `year:2020..2024` or `year:..2020`.

    Args:
        value: The document's value, as text.
        spec: The term's text.

    Returns:
        The comparison's outcome, or `None` when `spec` is not a range comparison at all, so the caller
        falls back to a substring test.
    """
    found = _RANGE.match(spec)
    if not found or not (found.group(1) or found.group(3)):
        return None
    try:
        number = float(re.sub(r"[^0-9.]", "", value) or "nan")
        low = float(found.group(2)) if found.group(2) else float("-inf")
        high = float(found.group(4)) if found.group(4) else float("inf")
    except ValueError:
        return None
    if found.group(3):  # a .. b
        return low <= number <= high
    operator, bound = found.group(1), low
    return {">": number > bound, ">=": number >= bound, "<": number < bound}.get(operator, number <= bound)


def match_text(text: str, terms: tuple[Term, ...], mode: str = "substring") -> bool:
    """Every term must hit the blob. For pickers, where there are no fields.

    Args:
        text: The blob to search.
        terms: The parsed terms.
        mode: `substring` or `fuzzy`.

    Returns:
        `True` when every term agrees, a negated term by missing.
    """
    folded_text = text.casefold()
    hit = fuzzy_match if mode == "fuzzy" else lambda needle, candidate: needle in candidate
    return all(hit(term.text, folded_text) != term.negate for term in terms)


def match_doc(doc: Document, terms: tuple[Term, ...], fields: list[str], mode: str) -> bool:
    """Every term must hit: a qualified term its own field, a bare term any of `fields`.

    Ranges only apply to a qualified term — `>2023` alone is a string.

    Args:
        doc: The document to test.
        terms: The parsed terms.
        fields: The keys a bare term searches.
        mode: `substring` or `fuzzy`.

    Returns:
        `True` when every term agrees, a negated term by missing.
    """
    blob = haystack(doc, fields)
    hit = fuzzy_match if mode == "fuzzy" else lambda needle, candidate: needle in candidate
    for term in terms:
        if not term.field:
            found = hit(term.text, blob)
        else:
            value = display(resolve(doc, term.field) or "")
            ranged = _in_range(value, term.text)
            found = ranged if ranged is not None else hit(term.text, shown_too(value).casefold())
        if found == term.negate:
            return False
    return True


def narrow(
    docs: list[Document],
    query: str,
    fields: list[str],
    mode: str,
    aliases: dict[str, str] | None = None,
) -> list[Document]:
    r"""Filter in memory.

    Terms are ANDed, so typing more always narrows — the old whole-query subsequence test left
    702 of 754 documents on `nauen`. Unparsable regex matches nothing rather than raising. A
    regex is compiled case-insensitive rather than casefolded, which would turn `\D` into
    `\doc`.

    Args:
        docs: The documents to filter.
        query: The narrow query as typed; blank keeps them all.
        fields: The keys to search.
        mode: `substring`, `fuzzy` or `regex`.
        aliases: Alias to the key it stands for, or `None`.

    Returns:
        The documents that match, in their given order.
    """
    if not query.strip():
        return docs
    if mode == "regex":
        try:
            pattern = re.compile(query, re.IGNORECASE | re.MULTILINE)  # `^` starts the shown line too
        except re.error:
            return []
        return [doc for doc in docs if pattern.search(haystack(doc, fields))]
    terms = parse_query(query, aliases)
    return [doc for doc in docs if match_doc(doc, terms, fields, mode)]


# ── sorting ─────────────────────────────────────────────────────────────────

# papis stamps `time-added` in the `papis add` command line, not in the
# `add.run()` ptui calls — so ptui stamps it itself, in papis's own format, or the
# shipped "recently added" sort would have nothing to order by.
TIME_ADDED = "time-added"


def stamp(when: float | None = None) -> str:
    """Render `time-added` for now, or for a POSIX timestamp.

    papis's format sorts lexically, which is why the value can stay a string.

    Args:
        when: Seconds since the epoch, or `None` for now.

    Returns:
        The time as papis's `time-added` text.
    """
    moment = datetime.now() if when is None else datetime.fromtimestamp(when)
    return moment.strftime(papis.strings.time_format)


def resolve(doc: Document, dotted: str) -> Any:
    """`author_list.0.family` -> the value, or None.

    Dotted paths matter because `author` is a formatted string and people want the first
    author's surname.

    Args:
        doc: The document to read.
        dotted: Dotted path of dict keys and list indexes.

    Returns:
        The value found, or `None` when any step of the path is missing.
    """
    node: Any = doc
    for part in dotted.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, (list, tuple)) and part.lstrip("-").isdigit():
            index = int(part)
            node = node[index] if -len(node) <= index < len(node) else None
        else:
            return None
        if node is None:
            return None
    return node


def _comparable(value: Any) -> tuple[int, float, str]:
    """Make mixed types orderable: numbers before strings, strings case-folded.

    Args:
        value: A stored value.

    Returns:
        A `(rank, number, text)` sort key.
    """
    if isinstance(value, bool):
        return (0, float(value), "")
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    text = str(value)
    try:
        return (0, float(text), "")
    except ValueError:
        return (1, 0.0, text.casefold())


def sort(
    docs: list[Document],
    key: str,
    *,
    reverse: bool = False,
    tiebreak: str | None = None,
    null_ordering: str = "last",
) -> list[Document]:
    """Sort, keeping documents that lack the key at one end whatever the direction.

    Args:
        docs: The documents to sort; left unchanged.
        key: Dotted path to sort by.
        reverse: Sort descending.
        tiebreak: Dotted path deciding ties, or `None`.
        null_ordering: `first` puts documents without the key first; anything else, last.

    Returns:
        A new sorted list.
    """
    if tiebreak and tiebreak != key:
        docs = sort(docs, tiebreak, null_ordering=null_ordering)  # stable: secondary first

    present = [doc for doc in docs if resolve(doc, key) is not None]
    missing = [doc for doc in docs if resolve(doc, key) is None]
    present.sort(key=lambda doc: _comparable(resolve(doc, key)), reverse=reverse)
    return missing + present if null_ordering == "first" else present + missing


def discover_keys(docs: list[Document]) -> list[str]:
    """Union of top-level keys across the library, for the sort picker.

    Args:
        docs: The documents to read.

    Returns:
        The keys, sorted, without those starting with `_`.
    """
    keys: set[str] = set()
    for doc in docs:
        keys |= {key for key in doc if not key.startswith("_")}
    return sorted(keys)


# ── display ─────────────────────────────────────────────────────────────────

# ponytail: brace/dollar stripping covers `{B}ERT` and `$\ell_2$` well enough;
# swap in pylatexenc if real macro rendering is ever wanted. Display only — the
# verbatim value is what gets stored, yanked and exported.
_LATEX = re.compile(r"[{}$]|\\[a-zA-Z]+")


def fit(text: str, width: int) -> str:
    """Truncate to `width` terminal cells, ellipsis included in the budget.

    Cells, not characters: CJK and emoji are two columns wide, combining marks
    are zero, and a list that counts characters overflows on a real library.

    The cut backs off to a word boundary — mid-word reads as a typo — and
    prefers the last colon, since a title's head is the informative part.
    Only when that boundary keeps most of the budget; otherwise the cut wins.

    Args:
        text: The text to cut.
        width: The budget in terminal cells.

    Returns:
        The text unchanged when it fits, else cut and ending in `…`; empty for no budget.
    """
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    head = set_cell_size(text, width - 1)
    for separator in (":", " "):
        cut = head.rfind(separator)
        if cut > 0 and cell_len(head[:cut]) >= (width - 1) * 0.6:
            return head[:cut].rstrip() + "…"
    return head + "…"


def wrap_cells(text: str, width: int) -> list[str]:
    """Greedy word wrap by terminal cells.

    A word wider than `width` gets its own row and is cut by the caller — splitting inside a
    word reads as corruption.

    Args:
        text: The text to wrap.
        width: The width of a row in terminal cells.

    Returns:
        The rows, in order.
    """
    rows: list[str] = []
    row = ""
    for word in text.split():
        candidate = f"{row} {word}" if row else word
        if cell_len(candidate) <= width:
            row = candidate
            continue
        if row:
            rows.append(row)
        row = word
    if row:
        rows.append(row)
    return rows


def fit_lines(text: str, width: int, lines: int) -> str:
    """Fit text into `lines` rows of `width` cells.

    The result is newline-joined and every row is within `width` cells, so the table never has to guess
    where to wrap.

    Wrapping ourselves rather than letting the widget do it is what keeps the
    ellipsis honest — a greedy wrap fits fewer cells than `width * lines`, and a
    budget computed from the product silently loses the last words instead.

    Args:
        text: The text to fit.
        width: The width of a row in terminal cells.
        lines: The number of rows.

    Returns:
        The rows joined by newlines; the last ends in `…` when text was lost.
    """
    if lines <= 1 or width <= 0:
        return fit(text, width)
    rows = wrap_cells(text, width)
    # Every row is cut, not just the last: a single word wider than the column
    # gets a row of its own and would otherwise overflow the cell unclipped.
    if len(rows) <= lines:
        return "\n".join(fit(row, width) for row in rows)
    kept = [fit(row, width) for row in rows[: lines - 1]]
    return "\n".join([*kept, fit(" ".join(rows[lines - 1 :]), width)])


def display(value: Any) -> str:
    """Display text for a stored value.

    Papis keeps `tags` as a list and `str()` would show the Python repr.

    Args:
        value: A stored value.

    Returns:
        The text; list items are joined by `, `, recursively.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(display(item) for item in value)
    return str(value)


def key_types() -> dict[str, str]:
    """Ask papis for the type it declares for each key (`document-field-types`).

    For example `tags:list`, `year:int`, `files:list`.

    Asked of papis rather than kept in a table here, so setting a field and
    the `field-type` check can never disagree — a `tags` written as a string is
    exactly what that check exists to complain about.

    Returns:
        Key to the name of its type.
    """
    return {key: cls.__name__ for key, cls in papis.document.get_document_field_types().items()}


def typed(key: str, text: str, current: Any = None) -> Any:
    """What a user typed, as the value that key should hold.

    `None` means *remove the key* — an empty value is how a field is cleared.

    The declared type wins; failing that, the type already stored under the key
    does, so a list stays a list even for a key papis has no opinion about.
    Everything else is text: guessing that `2024` in an undeclared field means a
    number is how `volume: 04` loses its zero. A list is split on commas alone,
    so a multi-word item survives being typed and the edit form's round trip.

    Args:
        key: The `info.yaml` key being set.
        text: What the user typed.
        current: The value stored today, or `None`.

    Returns:
        A list, bool, int, float or str, or `None` to remove the key.

    Raises:
        ValueError: When a number does not parse.
    """
    text = text.strip()
    if not text:
        return None
    kind = key_types().get(key)
    if kind is None and current is not None:
        # isinstance, not the type's name: read through ruamel, a float is a `ScalarFloat`
        kind = next((cls.__name__ for cls in (bool, int, float, list) if isinstance(current, cls)), None)
    if kind == "list":
        # commas only: `machine learning` is one tag, and `display` joins with ", "
        return [part.strip() for part in text.split(",") if part.strip()]
    if kind == "bool":
        return text.casefold() in {"true", "yes", "on", "1"}
    if kind in ("int", "float"):
        try:
            return int(text) if kind == "int" else float(text)
        except ValueError as error:
            raise ValueError(f"{key} must be {kind}, not {text!r}") from error
    return text


ARXIV_DOI = "10.48550"
"""arXiv mints its own DOIs under this prefix. A DOI under any other prefix was
assigned by a publisher, which is the strongest local evidence of publication."""

ARXIV_URL = "arxiv.org"


def on_arxiv(doc: Document) -> bool:
    """Look for arXiv evidence that is *not* a DOI.

    Only ever asked of a document that has no DOI at all, because `eprint` survives publication:
    measured on the real library, 182 documents carry a publisher DOI **and** an arXiv eprint, so this
    may never outrank one.

    `eprinttype` rather than the bare presence of `eprint`: every one of the 495
    eprints in that library names its server, and the ones that are not arXiv say
    `OpenAI Blog` or `openreview`, which are exactly the entries that must stay
    `article`. The URL is the fallback for an entry that recorded no eprint type.

    Args:
        doc: The document to read.

    Returns:
        `True` when its eprint type or URL names arXiv.
    """
    if str(doc.get("eprinttype") or "").casefold() == "arxiv":
        return True
    return ARXIV_URL in str(doc.get("url") or "").casefold()


VENUE_KEYS = ("booktitle", "journal", "journaltitle")
"""Where the *name* of a conference or journal lives, in preference order.

**Not `venue`.** Despite the word, that key holds the place a conference was
held — measured on a real library, its values are `New Orleans, Louisiana, USA`,
`Sydney, Australia`, `Vancouver, BC, Canada`. Reading it as a publication name
puts a city where the journal should be, and makes `kind()` call a preprint
published because somebody recorded a hotel.
"""


def venue(doc: Document) -> str:
    """The conference or journal name, or empty. A city is not a name.

    Args:
        doc: The document to read.

    Returns:
        The first non-blank value of `VENUE_KEYS`, stripped; empty when there is none.
    """
    return next((str(doc[key]).strip() for key in VENUE_KEYS if str(doc.get(key) or "").strip()), "")


def kind(doc: Document) -> str:
    """`type`, except that an arXiv-only article reads as `preprint`.

    Local data only, deliberately: an `article` that names no journal, booktitle
    or venue and whose only trace is arXiv's has nothing to say it was ever
    published. That is evidence of absence, not absence of publication — an
    entry imported from arXiv and never refreshed looks identical, so a paper
    that did appear at a conference is flagged until its metadata is updated.
    Settling it properly needs a network lookup; see TODO § D.

    The DOI decides whenever there is one: arXiv's prefix means preprint, any
    other prefix means a publisher minted it. Only a document with **no** DOI
    falls through to `on_arxiv`, which is what rescues the 19 arXiv imports that
    never recorded one — measured, they are all `eprinttype: arxiv`.

    A DOI stored as a `https://doi.org/…` or `dx.doi.org` URL is read as the
    bare DOI first, by `python-doi` (papis's own dependency, the extraction its
    downloaders use); otherwise arXiv's prefix hides behind the host name.

    Args:
        doc: The document to read.

    Returns:
        The casefolded type with papis's aliases applied, or `preprint`.
    """
    stored = str(doc.get("type") or "").strip().casefold()
    # papis's own alias table, the one its `biblatex-type-alias` check reads: a
    # `phdthesis` is a thesis, a `techreport` a report
    stored = papis.bibtex.bibtex_type_aliases.get(stored, stored)
    if stored != "article":
        return stored
    if venue(doc):
        return "article"
    doi = str(doc.get("doi") or "")
    if doi:
        doi = (find_doi_in_text(doi) or doi).casefold()
        return "preprint" if doi.startswith(ARXIV_DOI) else "article"
    return "preprint" if on_arxiv(doc) else "article"


STRUCTURAL = frozenset({"files"})
"""Keys that are data, not display text, and must survive `flatten` intact.

`files` is a list of paths that callers iterate. Joining it produced a string,
and every `for entry in doc["files"]` then walked it one character at a time —
which is exactly what the info pane did until this existed.
"""


def flatten(doc: Document) -> Document:
    """Prepare a document for `papis.format`.

    Scalar lists are joined and the derived `kind` added. Lists of dicts stay as they are:
    `{doc[author_list][0][family]}` still has to index.

    Args:
        doc: The document to read; left unchanged.

    Returns:
        A new `Document` with the flattened values.
    """
    data = {
        key: display(value)
        if key not in STRUCTURAL
        and isinstance(value, list)
        and all(isinstance(item, (str, int, float)) for item in value)
        else value
        for key, value in doc.items()
    }
    data["kind"] = kind(doc)  # derived, and wins over a stored `kind` for display
    data["venue"] = venue(doc)  # the *name*; the stored `venue` is a city, see VENUE_KEYS
    return Document(data=data)


def percentile_90(texts: list[str]) -> int:
    """Work out the width the 90th-percentile cell needs.

    Sizing a column to its longest value lets one outlier tax every row; sizing to the median truncates
    too much.

    Args:
        texts: The column's cells.

    Returns:
        The width in terminal cells, 0 for no cells.
    """
    widths = sorted(cell_len(text) for text in texts)
    return widths[math.ceil(0.9 * len(widths)) - 1] if widths else 0  # nearest rank


def strip_latex(text: str) -> str:
    """Remove braces, dollars and macro names, for display only.

    Args:
        text: The text as stored.

    Returns:
        The text without them, stripped of surrounding whitespace.
    """
    return _LATEX.sub("", text).strip()
