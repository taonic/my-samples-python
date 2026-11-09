"""The code behind each step of the UI's loop animation, colour-coded for display.

Read straight from the running modules with `inspect`, so the viewer always
shows the code that is actually executing.
"""

import builtins
import inspect
import io
import keyword
import textwrap
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Union

from . import activities
from .core import planner, resolver, surface
from .workflow import ProgressiveDataResolutionWorkflow as Workflow
from .workflow import WorkflowInput

PACKAGE = Path(__file__).parent


@dataclass
class Snippet:
    """Code `inspect` cannot fetch as one object: the lines of `path` from the one
    starting with `first` to the next one starting with `last`."""

    name: str
    path: str
    first: str
    last: str


INBOX_EVENTS = Snippet("inbox events", "workflow.py", "# Events on the inbox.", "_Event = ")
# A route defined inside create_app, so read by its lines.
START_ROUTE = Snippet(
    "POST /applications", "api.py", '    @app.post("/applications")', '        return {"workflowId"'
)

# Loop animation step -> the code that implements it, in reading order.
STEPS: dict[str, list[Union[Callable[..., Any], Snippet]]] = {
    "updates": [Workflow.submit, Workflow._validate_submit, Workflow._request],
    "loop": [Workflow._run_form, Workflow.run],
    # _apply turns each event into the trigger that _run_form reconciles on.
    "inbox": [Workflow._run_form, Workflow._apply, INBOX_EVENTS],
    "apply": [Workflow._apply, Workflow._apply_submission],
    "resolve": [Workflow._reconcile, resolver.resolve_requirements],
    "plan": [planner.plan_actions],
    "start": [Workflow._start_activity, Workflow._run_activity],
    "form": [surface.render_surface, planner.plan_actions],
    "activities": [activities.verify_identity, activities.assess_affordability],
    "exit": [resolver.select_next, Workflow._next_input],
    "load": [Workflow._load_schema, activities.load_schema],
    "begin": [START_ROUTE, WorkflowInput],
}

_STRING_TYPES = {tokenize.STRING} | {
    getattr(tokenize, name)
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END")
    if hasattr(tokenize, name)
}
_SKIP_AS_PREVIOUS = {
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.COMMENT,
    tokenize.INDENT,
    tokenize.DEDENT,
}


def step_source(step: str) -> list[dict[str, Any]]:
    """Each piece of code behind `step`: its name, file, first line and highlighted code."""
    parts = []
    for item in STEPS[step]:
        if isinstance(item, Snippet):
            name, file = item.name, item.path
            lines, first_line = _snippet_lines(item)
        else:
            name = item.__qualname__
            file = Path(inspect.getsourcefile(item) or "").relative_to(PACKAGE).as_posix()
            lines, first_line = inspect.getsourcelines(item)
        parts.append(
            {
                "name": name,
                "file": file,
                "line": first_line,
                "runs": highlight(textwrap.dedent("".join(lines))),
            }
        )
    return parts


def _snippet_lines(snippet: Snippet) -> tuple[list[str], int]:
    lines = (PACKAGE / snippet.path).read_text().splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith(snippet.first))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith(snippet.last))
    return lines[start : end + 1], start + 1


def highlight(code: str) -> list[list[str]]:
    """Splits Python source into [kind, text] runs that join back into `code`.

    Kinds: kw, str, com, num, fn (a name being defined), dec (decorator),
    self, bi (builtin), or "" for everything else.
    """
    offsets = [0]
    for line in code.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    runs: list[list[str]] = []
    done = 0
    previous = None
    decorator = False  # inside "@name.attr", up to its arguments or the line end
    for tok in tokenize.generate_tokens(io.StringIO(code).readline):
        start = offsets[tok.start[0] - 1] + tok.start[1]
        end = offsets[tok.end[0] - 1] + tok.end[1]
        if tok.type == tokenize.OP and tok.string == "@" and tok.line[: tok.start[1]].strip() == "":
            decorator = True
        elif tok.type in (tokenize.NL, tokenize.NEWLINE) or tok.string == "(":
            decorator = False
        if start > done:
            runs.append(["", code[done:start]])
        if end > start:
            kind = (
                "dec"
                if decorator and (tok.type == tokenize.NAME or tok.string in ("@", "."))
                else _kind(tok, previous)
            )
            runs.append([kind, code[start:end]])
        done = max(done, end)
        if tok.type not in _SKIP_AS_PREVIOUS:
            previous = tok
    if done < len(code):
        runs.append(["", code[done:]])
    return runs


def _kind(tok: tokenize.TokenInfo, previous: Any) -> str:
    if tok.type in _STRING_TYPES:
        return "str"
    if tok.type == tokenize.COMMENT:
        return "com"
    if tok.type == tokenize.NUMBER:
        return "num"
    if tok.type != tokenize.NAME:
        return ""
    if previous is not None and previous.string in ("def", "class"):
        return "fn"
    if keyword.iskeyword(tok.string):
        return "kw"
    if tok.string == "self":
        return "self"
    if hasattr(builtins, tok.string):
        return "bi"
    return ""
