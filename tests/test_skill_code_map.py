"""Skills are code maps: the ASTRA commands, workflow tools, MCP tools and plugin scripts they name must exist.

The scan reads inline code spans, links, and `astra` command lines and script paths in shell code blocks.
"""

from __future__ import annotations

import argparse
import difflib
import importlib.util
import inspect
import os
import re
import shlex
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import anyio
import pytest
from markdown_links import code_spans_with_siblings, fenced_blocks, link_targets

from astra_sumo import cli, legacy_tools
from astra_sumo.server import create_server


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "astra-sumo"
SKILLS = PLUGIN / "skills"
SCRIPTS = PLUGIN / "scripts"
_SUMO_SPEC = importlib.util.find_spec("sumo")
# SUMO's bundled tools directory, or None without the optional `sumo` package (never the working directory).
SUMO_TOOLS: Path | None = Path(_SUMO_SPEC.origin).parent / "tools" if _SUMO_SPEC and _SUMO_SPEC.origin else None
# Upstream MIT skill bodies bundled byte-for-byte in astra-report; they name no ASTRA code.
VENDORED = {"asd-ste100-skill.md", "humanizer-skill.md"}

PLACEHOLDER = re.compile(r"<[^<>]*>")
OPTION = re.compile(r"--[A-Za-z0-9][\w-]*")
IDENTIFIER = re.compile(r"[A-Za-z_]\w*")
KEYWORD = re.compile(r"([A-Za-z_]\w*)\s*=")
CALL = re.compile(r"([A-Za-z_]\w*)\((.*)\)", re.DOTALL)
# `tool` (arg, arg or arg) written in prose right after the tool's code span.
ARGUMENT_TAIL = re.compile(r"\s*\(([^()]*)\)")
MCP_NAME = re.compile(r"astra\.[a-z_.]+(?:/[a-z_]+)*")  # also `astra.netedit.open/close` shorthand
FILE_SUFFIXES = {"cfg", "csv", "exe", "html", "json", "lock", "md", "py", "sumocfg", "toml", "txt", "xml", "yaml", "yml"}
SCRIPT_PATH = re.compile(
    r"(?<![\w./-])(?:(?:<[^<>]*>|plugins/astra-sumo)/)?scripts/([\w-][\w./-]*\.py)(?![\w.-])"
)
NEAR_MISS = 0.8  # difflib ratio at which an unknown tool-like name reads as a misspelt tool
# Text between the code spans of an argument list: `a`, `b`, and `c` or `d`. A bare space is not one:
# `profile` `value` names a parameter and its value.
LIST_SEPARATOR = re.compile(r"\s*(?:,\s*(?:(?:and|or)\s+)?|(?:and|or)\s+)")
SHELL_FENCES = {"", "bash", "bat", "cmd", "console", "powershell", "ps", "ps1", "pwsh", "sh", "shell"}
PROMPT = re.compile(r"^(?:PS[^>]*>|[$>])\s*")
BARE_SCRIPT = re.compile(r"[\w.-]+\.py")
OTHER_COMMANDS = {"duarouter", "netconvert", "netedit", "polyconvert", "python", "sumo", "sumo-gui", "uv"}


@dataclass(frozen=True)
class CodeMap:
    parser: argparse.ArgumentParser  # the `astra` command line
    workflow_tools: frozenset[str]  # what `astra workflow <tool>` runs
    functions: Mapping[str, frozenset[str] | None]  # tool -> parameter names; None takes any keyword
    tool_prefixes: tuple[str, ...]
    mcp_tools: frozenset[str]
    scripts_dir: Path
    scripts: frozenset[str]  # relative to scripts_dir
    known_names: frozenset[str] = frozenset()  # identifiers in the product source and SUMO's tools, e.g. field names
    python_files: frozenset[str] = frozenset()  # *.py file names of SUMO's tools and of the repository


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _parameters(function: Callable) -> frozenset[str] | None:
    parameters = inspect.signature(function).parameters.values()
    if any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters):
        return None
    return frozenset(parameter.name for parameter in parameters if parameter.kind is not parameter.VAR_POSITIONAL)


def build_code_map(
    parser: argparse.ArgumentParser,
    workflow_functions: Mapping[str, Callable],
    dispatch_source: str,
    *,
    mcp_tools: Iterable[str],
    legacy_mcp_tools: Mapping[str, Iterable[str]],
    scripts_dir: Path,
    scripts: Iterable[str],
    known_names: Iterable[str] = (),
    python_files: Iterable[str] = (),
) -> CodeMap:
    """`astra workflow` runs a CLI tool choice that is a WORKFLOW_TOOLS key, or a literal the dispatcher handles itself."""
    workflow = _subcommands(parser)["workflow"]
    choices = next(action.choices for action in workflow._actions if not action.option_strings and action.choices)
    dispatched = set(re.findall(r"args\.tool\s*==\s*['\"]([\w-]+)['\"]", dispatch_source))
    functions: dict[str, frozenset[str] | None] = {name: _parameters(fn) for name, fn in workflow_functions.items()}
    for name, parameters in legacy_mcp_tools.items():
        functions.setdefault(name, frozenset(parameters))
    return CodeMap(
        parser=parser,
        workflow_tools=frozenset(name for name in choices if name in workflow_functions or name in dispatched),
        functions=functions,
        tool_prefixes=tuple(sorted({name.split("_", 1)[0] + "_" for name in workflow_functions})),
        mcp_tools=frozenset(mcp_tools),
        scripts_dir=scripts_dir,
        scripts=frozenset(scripts),
        known_names=frozenset(known_names),
        python_files=frozenset(python_files),
    )


@cache
def real_code_map() -> CodeMap:
    def tools(profile: str) -> list:
        return anyio.run(create_server(profile).list_tools)

    return build_code_map(
        cli._build_parser(),
        legacy_tools.WORKFLOW_TOOLS,
        inspect.getsource(cli._dispatch),
        mcp_tools=[tool.name for profile in ("default", "netedit") for tool in tools(profile)],
        legacy_mcp_tools={tool.name: tool.inputSchema.get("properties", {}) for tool in tools("legacy")},
        scripts_dir=SCRIPTS,
        scripts=[path.relative_to(SCRIPTS).as_posix() for path in SCRIPTS.rglob("*.py")],
        known_names={
            name
            for root in existing_roots(PLUGIN / "src", SUMO_TOOLS)
            for path in root.rglob("*.py")
            for name in IDENTIFIER.findall(path.read_text(encoding="utf-8", errors="replace"))
        },
        python_files={
            path.name
            for root in existing_roots(SUMO_TOOLS, ROOT / "benchmarks", ROOT / "tests", PLUGIN / "src")
            for path in root.rglob("*.py")
        },
    )


def existing_roots(*roots: Path | None) -> list[Path]:
    """The given directories that exist; an absent optional root (None) is skipped, never read as `.`."""
    return [root for root in roots if root is not None and root.is_dir()]


def _is_literal(token: str) -> bool:
    return not token.startswith("-") and "<>" not in token and token not in ("...", "…")


def _take_values(action: argparse.Action, tokens: list[str], index: int) -> list[str]:
    if action.nargs == 0:
        return []
    limit = action.nargs if isinstance(action.nargs, int) else None if action.nargs in ("*", "+", "...") else 1
    values = []
    while index + len(values) < len(tokens) and not tokens[index + len(values)].startswith("-"):
        values.append(tokens[index + len(values)])
        if limit is not None and len(values) == limit:
            break
    return values


def check_command(span: str, code_map: CodeMap) -> list[str]:
    """`astra <subcommand path> ...`: the path, every `--option` and option choices exist; so does a workflow tool."""
    text = PLACEHOLDER.sub("<>", span)
    try:
        tokens = shlex.split(text)[1:]
    except ValueError:
        tokens = text.split()[1:]

    parser, path, rest = code_map.parser, ["astra"], []
    for position, token in enumerate(tokens):
        child = _subcommands(parser).get(token)
        if child is not None:
            parser, path = child, [*path, token]
        elif token.startswith("-"):
            rest.append(token)  # an option before the subcommand path ends
        else:
            rest.extend(tokens[position:])
            break
    command = " ".join(path)
    options = {**code_map.parser._option_string_actions, **parser._option_string_actions}

    positionals, problems, index = [], [], 0
    while index < len(rest):
        token = rest[index]
        index += 1
        flag, has_value, value = token.strip("[](),;").partition("=")
        if not OPTION.fullmatch(flag):
            if not token.startswith("-"):
                positionals.append(token)
            continue
        action = options.get(flag)
        if action is None:
            problems.append(f"`{flag}` is not an option of `{command}`")
            continue
        values = [value] if has_value else _take_values(action, rest, index)
        index += 0 if has_value else len(values)
        if action.choices is not None:
            allowed = [str(choice) for choice in action.choices]
            for part in (part for value in values if _is_literal(value) for part in value.split("|")):
                if part not in allowed:
                    problems.append(f"`{flag} {part}` is not one of {', '.join(allowed)}")

    if _subcommands(parser) and positionals and _is_literal(positionals[0]):
        return [f"`{command}` has no subcommand `{positionals[0]}`"]
    if path == ["astra", "workflow"] and positionals and _is_literal(positionals[0]):
        if positionals[0] not in code_map.workflow_tools:
            problems.insert(0, f"`{positionals[0]}` is not a tool that `astra workflow` runs")
    return problems


def _call_arguments(text: str) -> list[str]:
    names = []
    for item in (item.strip() for item in text.split(",")):
        if IDENTIFIER.fullmatch(item):
            names.append(item)
        elif keyword := KEYWORD.match(item):
            names.append(keyword.group(1))
    return names


def _tail_arguments(tail: str) -> list[str] | None:
    """Names in a `(a, b or c)` list right after the span; None when the parentheses hold prose."""
    listed = ARGUMENT_TAIL.match(tail)
    if listed is None:
        return None
    items = [
        part.strip()
        for chunk in listed.group(1).split(",")
        for part in re.split(r"\s+(?:or|and)\s+", chunk)
        if part.strip() not in ("", "...", "…")
    ]
    if not items or not all(IDENTIFIER.fullmatch(item) for item in items) or not any("_" in item for item in items):
        return None
    return items


def _tail_text(siblings: list[tuple[str, str]]) -> str:
    """The plain text right after a span, up to the next markup."""
    tail = []
    for kind, content in siblings:
        if kind != "text":
            break
        tail.append(content)
    return "".join(tail)


def _code_arguments(siblings: list[tuple[str, str]]) -> list[str] | None:
    """`tool`: `a`, `b` or `tool` (`a`, `b`): the argument code spans listed right after the tool's span.

    An argument span may carry a value (`optimize: "full"`). Code spans inside a parenthesised aside such as
    (step 1 `route_candidate_manifest`) describe an argument and are not arguments themselves.
    """
    if not siblings or siblings[0][0] != "text" or not re.fullmatch(r":\s*|\s*\(\s*", siblings[0][1]):
        return None
    names, depth = [], 0
    for kind, content in siblings[1:]:
        if kind == "code_inline":
            if depth:
                continue
            argument = re.fullmatch(r"([A-Za-z_]\w*)(?:\s*[:=].*)?", content, re.DOTALL)
            if argument is None:
                break
            names.append(argument.group(1))
            continue
        if kind != "text":
            break
        outside, has_aside = [], False
        for character in content:
            if character == "(":
                depth, has_aside = depth + 1, True
            elif character == ")" and depth:
                depth, has_aside = depth - 1, True
            elif not depth:
                outside.append(character)
        between = "".join(outside)
        if not (LIST_SEPARATOR.fullmatch(between) or (has_aside and not between.strip())):
            break
    return names or None


def function_arguments(span: str, siblings: list[tuple[str, str]]) -> tuple[str | None, list[str] | None]:
    """(name, listed arguments) for `tool(arg, ...)`, `tool` (arg, ...), `tool` (`arg`, ...) or `tool`: `arg`, ..."""
    call = CALL.fullmatch(span.strip())
    if call is not None:
        return call.group(1), _call_arguments(call.group(2))
    if IDENTIFIER.fullmatch(span):
        return span, _tail_arguments(_tail_text(siblings)) or _code_arguments(siblings)
    return None, None


def check_function(span: str, siblings: list[tuple[str, str]], code_map: CodeMap) -> list[str]:
    """A workflow-tool name, `tool(arg, ...)`, `tool` (arg, ...), `tool` (`arg`, ...) or `tool`: `arg`, ...:
    the tool exists and takes those arguments."""
    call = CALL.fullmatch(span.strip())
    name, arguments = function_arguments(span, siblings)
    if name is None:
        return []
    if not name.startswith(code_map.tool_prefixes):
        return []
    if name not in code_map.functions:
        if call is not None or arguments is not None:
            return [f"`{name}` is not a workflow tool or MCP tool"]
        if name in code_map.known_names:
            return []  # a field or column name the code uses, not a misspelt tool
        near = difflib.get_close_matches(name, list(code_map.functions), n=1, cutoff=NEAR_MISS)
        return [f"`{name}` is not a workflow tool or MCP tool; did you mean `{near[0]}`?"] if near else []
    parameters = code_map.functions[name]
    if parameters is None:
        return []
    return [f"`{name}` has no parameter `{argument}`" for argument in arguments or [] if argument not in parameters]


def check_mcp(span: str, code_map: CodeMap) -> list[str]:
    """`astra.group.action`: a tool of the default or netedit MCP profile."""
    if not MCP_NAME.fullmatch(span):
        return []
    first, *alternatives = span.split("/")
    group, _, last = first.rpartition(".")
    if not alternatives and last in FILE_SUFFIXES:
        return []  # a file name such as astra.exe
    names = [first, *(f"{group}.{alternative}" for alternative in alternatives)]
    return [f"`{name}` is not an MCP tool of the default or netedit profile" for name in names if name not in code_map.mcp_tools]


def check_scripts(text: str, code_map: CodeMap) -> list[str]:
    """`scripts/<file>.py` or `plugins/astra-sumo/scripts/<file>.py`: the plugin script exists."""
    return [
        f"plugins/astra-sumo/scripts/{name} does not exist"
        for name in SCRIPT_PATH.findall(text.replace("\\", "/"))
        if name not in code_map.scripts
    ]


def check_bare_script(span: str, code_map: CodeMap) -> list[str]:
    """A span that starts with a bare `<file>.py` names a plugin script, a SUMO tool or a repository file."""
    first = span.split()[0] if span.split() else ""
    if not BARE_SCRIPT.fullmatch(first):
        return []
    if first in {Path(name).name for name in code_map.scripts} or first in code_map.python_files:
        return []
    return [f"`{first}` is not a plugin script, a SUMO tool or a repository file"]


def check_vocabulary(span: str, defined: set[str], code_map: CodeMap) -> list[str]:
    """An identifier with an underscore names something real: a tool, a parameter, or a name the code or SUMO
    uses (fields, columns, statuses), or a term this file defines in a table's first column. Misspelt arguments
    are caught here whatever prose surrounds them."""
    if not IDENTIFIER.fullmatch(span) or "_" not in span or span in defined:
        return []
    parameters = {name for names in code_map.functions.values() if names for name in names}
    if span in code_map.known_names or span in code_map.functions or span in parameters:
        return []
    return [f"`{span}` is not a name the ASTRA code or SUMO uses"]


def _top_level_keys(span: str) -> list[str]:
    keys, depth = [], 0
    for match in re.finditer(r'[{}\[\]]|"([^"\\]*)"\s*:', span):
        token = match.group(0)
        if token in "{[":
            depth += 1
        elif token in "}]":
            depth -= 1
        elif depth == 1:
            keys.append(match.group(1))
    return keys


def check_request(span: str, tool: str | None, defined: set[str], code_map: CodeMap) -> list[str]:
    """An inline JSON request `{"key": ...}`: with a workflow tool in the same block, its keys are that tool's
    parameters; otherwise each key must be a real name."""
    if not span.lstrip().startswith("{"):
        return []
    keys = _top_level_keys(span)
    parameters = code_map.functions.get(tool) if tool else None
    # An object with none of the tool's parameters is a nested item (e.g. one ledger operation), not the request.
    if parameters is not None and any(key in parameters for key in keys):
        return [f"`{tool}` request has no key `{key}`" for key in keys if key not in parameters]
    return [reason for key in keys for reason in check_vocabulary(key, defined, code_map)]


def astra_command_path(span: str, code_map: CodeMap) -> str:
    """The `astra <subcommand> ...` path at the start of a command span, without its arguments."""
    parser, path = code_map.parser, ["astra"]
    for token in PLACEHOLDER.sub("<>", span).split()[1:]:
        child = _subcommands(parser).get(token)
        if child is None:
            if token.startswith("-"):
                continue
            break
        parser, path = child, [*path, token]
    return " ".join(path)


def check_link(target: str, directory: Path, code_map: CodeMap) -> list[str]:
    problems = check_scripts(target, code_map)
    resolved = Path(os.path.normpath(directory / target))
    scripts_dir = Path(os.path.normpath(code_map.scripts_dir))
    if resolved.suffix == ".py" and resolved.is_relative_to(scripts_dir):
        name = resolved.relative_to(scripts_dir).as_posix()
        if name not in code_map.scripts:
            problems.append(f"plugins/astra-sumo/scripts/{name} does not exist")
    return list(dict.fromkeys(problems))


def shell_command_lines(content: str) -> list[str]:
    """Lines of a shell block with prompts removed and trailing-backslash or backtick continuations joined."""
    lines, pending = [], ""
    for raw in content.splitlines():
        line = raw.strip() if pending else PROMPT.sub("", raw.strip())
        if line.endswith(("`", "\\")):
            pending += line[:-1] + " "
            continue
        lines.append(pending + line)
        pending = ""
    return lines + ([pending] if pending else [])


def scan_markdown(text: str, source: Path, code_map: CodeMap) -> list[tuple[str, str]]:
    """(span, command line or link target, reason) for every name in `text` that the code does not have."""
    found = []
    # The command a standalone `--option` span belongs to: the latest command span in the same block, if it is
    # an `astra` command (options after a script, SUMO or netconvert command are not ours to check).
    command_in_block: dict[int, str | None] = {}
    tool_in_block: dict[int, str] = {}  # the latest workflow tool named in the block, for inline requests
    listed_arguments: dict[int, set[str]] = {}  # argument spans already checked against a tool signature
    defined = set(re.findall(r"^\|\s*`([A-Za-z_]\w*)`\s*\|", text, re.MULTILINE))
    for span, siblings, block in code_spans_with_siblings(text):
        reasons = check_command(span, code_map) if span.startswith("astra ") else []
        tokens = span.split()
        first = tokens[0] if tokens else ""
        if span.startswith("astra "):
            command_in_block[block] = astra_command_path(span, code_map)
            if tokens[1:2] == ["workflow"] and len(tokens) > 2:
                tool_in_block[block] = tokens[2]
        elif first in OTHER_COMMANDS or "/" in first or BARE_SCRIPT.fullmatch(first):
            command_in_block[block] = None
        elif OPTION.match(span) and command_in_block.get(block):
            reasons += check_command(f"{command_in_block[block]} {span}", code_map)
        if span in code_map.functions:
            tool_in_block[block] = span
        name, arguments = function_arguments(span, siblings)
        if name in code_map.functions and arguments:
            listed_arguments.setdefault(block, set()).update(arguments)  # checked against the signature
        function_reasons = check_function(span, siblings, code_map)
        reasons += function_reasons + check_mcp(span, code_map) + check_scripts(span, code_map)
        reasons += check_bare_script(span, code_map)
        reasons += check_request(span, tool_in_block.get(block), defined, code_map)
        if not function_reasons and span not in listed_arguments.get(block, set()):
            reasons += check_vocabulary(span, defined, code_map)
        found += [(span, reason) for reason in reasons]
    for info, content in fenced_blocks(text):
        if (info.split()[0].lower() if info else "") in SHELL_FENCES:
            for line in shell_command_lines(content):
                if line.startswith("astra "):
                    found += [(line, reason) for reason in check_command(line, code_map)]
        found += [(content.strip(), reason) for reason in check_scripts(content, code_map)]
    for _, target in link_targets(text):
        found += [(target, reason) for reason in check_link(target, source.parent, code_map)]
    return list(dict.fromkeys(found))


def skill_files() -> list[Path]:
    return [path for path in sorted(SKILLS.rglob("*.md")) if path.name not in VENDORED]


def test_skills_name_only_existing_code() -> None:
    files = skill_files()
    assert files
    failures = [
        f"{path.relative_to(ROOT).as_posix()}:{span} -> {reason}"
        for path in files
        for span, reason in scan_markdown(path.read_text(encoding="utf-8"), path, real_code_map())
    ]
    assert not failures, "\n" + "\n".join(failures)


def test_missing_sumo_package_adds_no_vocabulary_from_the_working_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "planted.py").write_text("net_flie = 1\n", encoding="utf-8")
    assert existing_roots(PLUGIN / "src", None) == [PLUGIN / "src"]
    assert existing_roots(None) == []


def test_real_code_map_reads_every_interface() -> None:
    code_map = real_code_map()
    assert set(legacy_tools.WORKFLOW_TOOLS) <= code_map.workflow_tools
    assert code_map.mcp_tools and code_map.scripts


# The scanner itself, on an in-memory code map.

def _sample_tool(net_file: str, output_dir: str, prefix: str = "sample") -> dict:
    return {}


def _open_tool(output_dir: str, **options: object) -> dict:
    return {}


def sample_code_map(dispatch_source: str = "if args.tool == 'selected':") -> CodeMap:
    parser = argparse.ArgumentParser(prog="astra")
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    network = commands.add_parser("network").add_subparsers(dest="network_command", required=True)
    audit = network.add_parser("audit")
    audit.add_argument("net_file")
    audit.add_argument("--profile", choices=("quick", "standard"))
    audit.add_argument("--junction-ids", nargs="*")
    workflow = commands.add_parser("workflow")
    workflow.add_argument("tool", choices=("sumo_sample", "sumo_unwired", "selected"))
    workflow.add_argument("request_file")
    workflow.add_argument("--execute", action="store_true")
    return build_code_map(
        parser,
        {"sumo_sample": _sample_tool, "sumo_open": _open_tool},
        dispatch_source,
        mcp_tools={"astra.network.audit", "astra.netedit.open", "astra.netedit.close"},
        legacy_mcp_tools={"sumo_legacy_only": ["net_file"]},
        scripts_dir=Path("/plugin/scripts"),
        scripts={"run.py", "sub/tool.py"},
        known_names={"sumo_samples", "sumo_node_id", "sumo_link", "sumo_route_file", "sumo_entry_v1"},
        python_files={"routeSampler.py", "helper.py"},
    )


SAMPLE_SOURCE = Path("/plugin/skills/demo/SKILL.md")


def scan(markdown: str, code_map: CodeMap | None = None) -> list[str]:
    return [reason for _, reason in scan_markdown(markdown, SAMPLE_SOURCE, code_map or sample_code_map())]


@pytest.mark.parametrize(
    "markdown",
    [
        # (a) astra commands
        "`astra network audit <net.xml> --profile quick --json`",
        "`astra --json network audit x` and `astra <command> ... --json`",
        "`astra network audit x --junction-ids a b --profile=quick|standard`",
        "`astra workflow sumo_sample request.json --json`, `astra workflow <tool> <request.json>`",
        "`astra workflow selected selection.json --execute`",
        "```powershell\nPS> astra network audit `\n  x --profile quick\n.venv/Scripts/python.exe plugins/astra-sumo/scripts/run.py\n```",
        '```json\n{"note": "astra frobnicate --typo"}\n```',
        # (b) workflow tools
        "`sumo_sample`, `sumo_sample(net_file, output_dir, prefix='a')` and `print(x)`",
        "`sumo_sample` (net_file, output_dir or prefix) then `sumo_sample` (optional)",
        "`sumo_open(anything)`, `sumo_legacy_only` (net_file) and the field `sumo_node_id`",
        "the CSV column `sumo_samples`, a near miss of a tool that the source code uses as a field",
        "`sumo_sample`: `net_file`, `output_dir` and `prefix`. The CSV has `sumo_node_id`, `sumo_link`.",
        "`sumo_sample` (`net_file`, `output_dir`), then read `status`",
        "`sumo_sample` (`prefix` `sumo_entry_v1` for the entry arms)",
        "`sumo_sample`: `net_file` (from step 1 `sumo_route_file`), `output_dir`, `prefix: \"a\"` (optional). Read `ok`.",
        "`astra network audit <net.xml>`; add `--profile quick` or repeat `--junction-ids`",
        "`scripts/run.py <dir>` with `--any-script-flag`, then `sumo -c x.sumocfg` and `--summary-output`",
        "`run.py`, `routeSampler.py` and `helper.py` exist",
        "`astra workflow sumo_sample <r.json>` with `net_file`, optional `output_dir`; read `sumo_link`",
        '`astra workflow sumo_sample <r.json>` with `{"net_file": "<net>", "prefix": "a"}`',
        'a ledger item `{"sumo_node_id": "<id>", "sumo_link": 1}` after `sumo_sample`',
        "| `own_label` | a term this file defines |\n|---|---|\n\nUse `own_label`.",
        # (c) MCP tools
        "`astra.network.audit`, `astra.netedit.open/close`, `astra.exe` and `astra.*`",
        # (d) plugin scripts
        "`plugins/astra-sumo/scripts/run.py --flag` `<plugin-dir>/scripts/run.py` `scripts/sub/tool.py`",
        "`docs/scripts/other.py` and [the runner](../../scripts/run.py)",
    ],
)
def test_scanner_accepts_names_the_code_has(markdown: str) -> None:
    assert scan(markdown) == []


@pytest.mark.parametrize(
    ("markdown", "reason"),
    [
        # (a) astra commands
        ("`astra network audiit x --profile quick`", "`astra network` has no subcommand `audiit`"),
        ("`astra network audit x --profil quick`", "`--profil` is not an option of `astra network audit`"),
        ("`astra network audit x --profile fast`", "`--profile fast` is not one of quick, standard"),
        ("`astra workflow sumo_missing request.json`", "`sumo_missing` is not a tool that `astra workflow` runs"),
        ("`astra workflow sumo_unwired request.json`", "`sumo_unwired` is not a tool that `astra workflow` runs"),
        # (b) workflow tools
        ("`sumo_sample(net_file, output)`", "`sumo_sample` has no parameter `output`"),
        ("`sumo_sample` (net_file, out_dir)", "`sumo_sample` has no parameter `out_dir`"),
        ("`sumo_legacy_only(output_dir)`", "`sumo_legacy_only` has no parameter `output_dir`"),
        ("`sumo_missing(net_file)`", "`sumo_missing` is not a workflow tool or MCP tool"),
        ("`sumo_sampel`", "`sumo_sampel` is not a workflow tool or MCP tool; did you mean `sumo_sample`?"),
        ("`sumo_sample`: `net_file`, `out_dir`, `prefix`", "`sumo_sample` has no parameter `out_dir`"),
        ("`sumo_sample` (`net_typo`, `output_dir`)", "`sumo_sample` has no parameter `net_typo`"),
        ("`sumo_sample`: `net_file` (from step 1), `out_typo`", "`sumo_sample` has no parameter `out_typo`"),
        ("`sumo_sample`: `net_file`, `prefx: \"a\"`", "`sumo_sample` has no parameter `prefx`"),
        ("`astra network audit <net.xml>`; add `--profil quick`", "`--profil` is not an option of `astra network audit`"),
        ("`run_typo.py <dir>`", "`run_typo.py` is not a plugin script, a SUMO tool or a repository file"),
        ("`astra workflow sumo_sample <r.json>` with `net_flie`", "`net_flie` is not a name the ASTRA code or SUMO uses"),
        ("then optional `sumo_lnik`", "`sumo_lnik` is not a name the ASTRA code or SUMO uses"),
        ('`sumo_sample` with `{"net_file": "<net>", "prefx": "a"}`', "`sumo_sample` request has no key `prefx`"),
        ('`{"sumo_node_ide": 1}`', "`sumo_node_ide` is not a name the ASTRA code or SUMO uses"),
        ("```bash\nastra network audit x --profil quick\n```", "`--profil` is not an option of `astra network audit`"),
        ("```\n> astra network frobnicate --not-a-flag\n```", "`astra network` has no subcommand `frobnicate`"),
        ("```powershell\nastra network audit `\n  x --profile fast\n```", "`--profile fast` is not one of quick, standard"),
        ("```text\nplugins/astra-sumo/scripts/gone.py\n```", "plugins/astra-sumo/scripts/gone.py does not exist"),
        # (c) MCP tools
        ("`astra.network.inspect`", "`astra.network.inspect` is not an MCP tool of the default or netedit profile"),
        ("`astra.netedit.open/act`", "`astra.netedit.act` is not an MCP tool of the default or netedit profile"),
        # (d) plugin scripts
        ("`scripts/missing.py`", "plugins/astra-sumo/scripts/missing.py does not exist"),
        (
            "`.venv/Scripts/python.exe plugins/astra-sumo/scripts/gone.py --out-dir <dir>`",
            "plugins/astra-sumo/scripts/gone.py does not exist",
        ),
        ("[gone](../../scripts/gone.py)", "plugins/astra-sumo/scripts/gone.py does not exist"),
    ],
)
def test_scanner_reports_names_the_code_lacks(markdown: str, reason: str) -> None:
    assert scan(markdown) == [reason]


def test_workflow_literal_counts_only_when_the_cli_dispatches_it() -> None:
    assert scan("`astra workflow selected selection.json`", sample_code_map(dispatch_source="")) == [
        "`selected` is not a tool that `astra workflow` runs"
    ]
