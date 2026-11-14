"""
cmd.exe's language, played against a world that isn't the PC.

A mod that installs itself with a .bat has its whole contract in that .bat:
where it looks for the game, what it asks, which of its tools it runs on what,
and what it copies where. Running it for real means somebody's script loose
on the game's archives with nothing recording what was under, so this plays it
instead. Every file it touches is the world's (sandbox.py hands it the drop
and a view of the game it can't write through), every question it asks goes
to the world too, and the programs it starts are the world's to run.

What's here is the language as cmd parses it rather than as its help says:

    phase 1   %var%, %1, %~dp0 and %%, expanded a line at a time as it's
              read, a whole ( ) block at once, which is why %errorlevel% in
              one is stale and why the scripts that know reach for !var!
    phase 2   quotes, ^, redirections, & && || |, blocks, then IF and FOR,
              which take the rest of their line, & and all, as their command
    later     FOR's %%a and delayed !var!, per command as it runs, after the
              line's been split up, so a value holding & is never a command

Goto looks forward from where the script is and wraps once, like cmd. A call
of a label is a new frame over the same text, a call of a .bat a new script,
and both end in an implicit endlocal. A syntax error ends the script the way
it ends cmd's, and anything the language has that this doesn't raises
Unsupported naming it, rather than guessing.
"""

import fnmatch
import re
from dataclasses import dataclass, field

# statements a run may take before it's called a loop. A for /r over a few
# thousand files with a handful of lines each is a few hundred thousand
LIMIT = 3_000_000
# what a program cmd can't find leaves in errorlevel
NOT_FOUND = 9009
# a file past this is an archive, not text a script reads line by line, and
# find *.bin over the game's would read gigabytes into memory
TEXT_LIMIT = 64 << 20
DEEPER = "it calls itself deeper and deeper without coming back"
# one check come out the same this many times, with no file or variable
# changed in between and no FOR going through its list, is a script waiting
# on something that never comes, there being nothing else on a copy to change
# it. SPUN_ANYWAY times is one too when all it changed was what it had
# changed before: a log it adds a line to, a count it keeps. A loop that
# copies a new file each time round is getting somewhere however long it is
SPIN = 1000
SPUN_ANYWAY = 10_000
SPUN = "it went round a loop checking the same thing over and over, waiting on something"
DELIMS = " \t,;="
# what start opens in the browser
URL = re.compile(r"^(https?://|www\.)", re.I)


class BatchError(Exception):
    pass


class Unsupported(BatchError):
    """something the script does that this doesn't play, said as what it is"""


class Endless(BatchError):
    pass


class Syntax(BatchError):
    """what cmd stops a whole script for: an IF with nothing on one side, say"""


@dataclass
class Ask:
    """a question the script puts to whoever runs it"""
    kind: str                 # choice or line
    prompt: str
    options: str = ""         # choice's letters, in order
    var: str = ""             # what set /p puts the answer in
    where: tuple = ()         # (script, line) it's asked at
    lines: tuple = ()         # what it had put on screen just before, a menu say
    frame: str = ""           # the call it's in, so a menu met again is known
    new: tuple = ()           # of those, what it put up since the last question


@dataclass
class Ran:
    """a program a play started, or tried to"""
    name: str
    text: str
    code: int
    how: str          # cmd's own (find, where), the PC's (reg, net), program, builtin, call or missing
    fed: str = ""     # the start of what was piped into it
    piped: object = None    # the Ran that fed it, when one did
    cwd: str = ""     # where it was run from, which a relative name like SupportFiles\x.exe needs


@dataclass
class Step:
    """
    one thing a play looked at or ran, kept so a play that went nowhere can
    be read back for why. `leaves` is whether what the script did about it
    sent it off, a goto or an exit
    """
    kind: str         # exist, defined, compare, errorlevel, status, ran, copy, opened
    what: object      # a path, a variable, the two sides compared, or the Ran it's about
    ok: bool          # the check as asked, before any NOT, or whether it ran clean
    leaves: bool = False
    line: str = ""    # its line in the script, as written
    sets: str = ""    # for a look that would set a variable on a find, that variable
    depth: int = 1    # 1 in the script itself, more inside a call, whose %1 is the call's
    # the check whose way off this was on: a message box before its exit is
    # that check giving up, not work the script went on to
    within: object = None


@dataclass
class Script:
    path: str
    lines: list
    labels: dict = field(default_factory=dict)    # label -> the lines declaring it

    @classmethod
    def read(cls, path: str, data: bytes) -> "Script":
        text = data.decode("utf-8", "surrogateescape")
        if text.startswith("\ufeff"):
            text = text[1:]
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        found = {}
        for n, line in enumerate(lines):
            name = _label(line)
            if name is not None:
                found.setdefault(name, []).append(n)
        return cls(path=path, lines=lines, labels=found)


def _label(line: str):
    stripped = line.lstrip(" \t@")
    if not stripped.startswith(":"):
        return None
    rest = stripped[1:].lstrip(" \t")
    return re.split(r"[ \t:+,;=]", rest, 1)[0].lower() if rest else ""


def decode(data: bytes) -> str:
    """a file's bytes as text that goes back to the same bytes, whatever they were"""
    return data.decode("utf-8", "surrogateescape")


def encode(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def norm(path: str) -> str:
    """
    an absolute Windows path the way Windows reaches it: one backslash a
    part, . and .. worked out, the trailing dots and spaces it drops
    """
    path = path.replace("/", "\\")
    drive, rest = (path[:2], path[2:]) if re.match(r"^[A-Za-z]:", path) else ("", path)
    parts = []
    for part in rest.split("\\"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        part = part.rstrip(" .")
        if part:
            parts.append(part)
    return (drive.upper() if drive else "") + "\\" + "\\".join(parts)


def join(cwd: str, path: str) -> str:
    path = path.replace("/", "\\")
    if re.match(r"^[A-Za-z]:\\", path):
        return norm(path)
    if re.match(r"^[A-Za-z]:", path):
        # C:foo is relative to that drive's own current folder, which nothing
        # here tracks. Its root is what every script means by it
        return norm(path[:2] + "\\" + path[2:])
    if path.startswith("\\"):
        return norm(cwd[:2] + path)
    return norm(cwd + "\\" + path)


def key(path: str) -> str:
    return norm(path).lower()


def parent(path: str) -> str:
    path = norm(path)
    head = path.rsplit("\\", 1)[0]
    return head + "\\" if re.fullmatch(r"[A-Za-z]:", head) else head or path


def leaf(path: str) -> str:
    return norm(path).rsplit("\\", 1)[-1]


def child(folder: str, name: str) -> str:
    return folder.rstrip("\\") + "\\" + name


def unquote(word: str) -> str:
    return word.replace('"', "")


def wild(name: str) -> bool:
    return "*" in name or "?" in name


def matches(name: str, pattern: str) -> bool:
    """a file name against a cmd wildcard: no classes, *.* is everything, a
    name with no dot included"""
    pattern = pattern.lower()
    if pattern in ("*.*", "*"):
        return True
    if pattern.endswith(".*") and "." not in name and matches(name, pattern[:-2]):
        return True
    escaped = "".join("[[]" if c == "[" else "[]]" if c == "]" else c for c in pattern)
    return fnmatch.fnmatchcase(name.lower(), escaped)


def mask(name: str, pattern: str) -> str:
    """
    the name copy and ren give a file for a target with wildcards in it, as
    cmd works it out rather than as anyone would guess: *x takes the name up
    to its last x, a letter steps over one of the name's, ? keeps one, and a
    dot jumps to the name's last dot
    """
    out, at, n = [], 0, 0
    while n < len(pattern):
        c = pattern[n]
        if c == "?":
            if at < len(name) and name[at] != ".":
                out.append(name[at])
                at += 1
        elif c == "*":
            if n + 1 == len(pattern):
                out.append(name[at:])
                at = len(name)
            elif pattern[n + 1] not in "*?":
                # the one place Windows matches a name's case
                found = name.rfind(pattern[n + 1], at)
                if found < 0:
                    out.append(name[at:])
                    at = len(name)
                else:
                    out.append(name[at:found])
                    at = found
        elif c == ".":
            dot = name.rfind(".", at)
            at = dot + 1 if dot >= 0 else len(name)
            out.append(".")
        else:
            out.append(c)
            if at < len(name) and name[at] != ".":
                at += 1
        n += 1
    return "".join(out).rstrip(".")


def split_args(text: str) -> list:
    """a command line as a C program's argv has it, the rules every exe here was built with"""
    out, word, quoted, slashes, any_ = [], [], False, 0, False
    for c in text:
        if c == "\\":
            slashes += 1
            continue
        if c == '"':
            word.append("\\" * (slashes // 2))
            if slashes % 2:
                word.append('"')
            else:
                quoted = not quoted
            slashes, any_ = 0, True
            continue
        if slashes:
            word.append("\\" * slashes)
            slashes = 0
        if c in " \t" and not quoted:
            if word or any_:
                out.append("".join(word))
            word, any_ = [], False
            continue
        word.append(c)
    if slashes:
        word.append("\\" * slashes)
    if word or any_:
        out.append("".join(word))
    return out


def batch_args(text: str) -> list:
    """%1 to %9 as a .bat gets them: quotes kept, spaces , ; = between"""
    out, word, quoted = [], [], False
    for c in text:
        if c == '"':
            quoted = not quoted
            word.append(c)
        elif c in DELIMS and not quoted:
            if word:
                out.append("".join(word))
                word = []
        else:
            word.append(c)
    if word:
        out.append("".join(word))
    return out


@dataclass
class Redirect:
    handle: int
    mode: str        # > >> < >& or <&
    target: str


@dataclass
class Simple:
    words: list      # raw words, quotes kept, carets taken
    tail: str        # everything after the first word, its delimiter included
    redirects: list = field(default_factory=list)


@dataclass
class Block:
    body: object     # Seq
    redirects: list = field(default_factory=list)


@dataclass
class If:
    negate: bool
    fold: bool
    kind: str        # errorlevel exist defined compare cmdextversion
    args: list
    then: object
    other: object = None


@dataclass
class For:
    switch: str      # "" d r dr l f
    root: str
    options: str
    var: str
    items: str
    body: object


@dataclass
class Pipe:
    commands: list


@dataclass
class Seq:
    items: list      # [(op, Pipe)], op "" & && or ||


def _leaves(node) -> bool:
    """whether a command takes the script elsewhere: a goto, an exit, a call to a label"""
    if isinstance(node, Simple):
        first = node.words[0].lstrip("@").lower() if node.words else ""
        if re.match(r"(goto|exit)\b", first):
            return True
        return first == "call" and node.tail.strip().startswith(":")
    if isinstance(node, Block):
        return _leaves(node.body)
    if isinstance(node, Seq):
        return any(_leaves(pipe) for _, pipe in node.items)
    if isinstance(node, Pipe):
        return any(_leaves(command) for command in node.commands)
    if isinstance(node, If):
        return _leaves(node.then) or (node.other is not None and _leaves(node.other))
    if isinstance(node, For):
        return _leaves(node.body)
    return False


def _gives_up(step: Step, taken, failed: bool = False) -> bool:
    """
    whether a check's branch is it going without what it looked for: a miss,
    a program that failed. The branch a find takes, its work and a goto past
    the rest, is it going on. A compare says neither on its own: it's giving
    up when it ends the script on the spot, when it found a variable empty
    (`if "%game%"==""` is `if not defined game`), or when it's on the
    errorlevel of a program that failed
    """
    if step.kind in ("exist", "defined", "status"):
        return not step.ok
    if step.kind == "errorlevel":
        return step.ok
    return _exits(taken) or step.ok and _empty(step.what) or failed and bool(
        re.search(r"errorlevel", step.line, re.I))


def _empty(sides) -> bool:
    """both sides of a compare nothing once their quotes or brackets are off"""
    return isinstance(sides, tuple) and not any(
        str(side).strip().strip('"').strip("[]{}()'").strip() for side in sides)


def _exits(node) -> bool:
    """whether a command ends the script or the call it's in: an exit, a goto :eof"""
    if isinstance(node, Simple):
        first = node.words[0].lstrip("@").lower() if node.words else ""
        # exit/b as often as exit /b
        return bool(re.match(r"exit\b", first)) or bool(
            re.match(r"goto\s*:?eof$", (first + " " + node.tail).strip().lower()))
    if isinstance(node, Block):
        return _exits(node.body)
    if isinstance(node, Seq):
        return any(_exits(pipe) for _, pipe in node.items)
    if isinstance(node, Pipe):
        return any(_exits(command) for command in node.commands)
    if isinstance(node, If):
        return _exits(node.then) or (node.other is not None and _exits(node.other))
    return False


def _sets(node) -> str:
    """the variable a command sets with a plain SET, "" for none"""
    if isinstance(node, Simple):
        if not node.words or node.words[0].lstrip("@").lower() != "set":
            return ""
        assigned = re.match(r'\s*"?([^=/"\s][^="]*)=', node.tail)
        return assigned.group(1).strip() if assigned else ""
    if isinstance(node, Block):
        return _sets(node.body)
    if isinstance(node, Seq):
        return next((v for _, pipe in node.items if (v := _sets(pipe))), "")
    if isinstance(node, Pipe):
        return next((v for command in node.commands if (v := _sets(command))), "")
    return ""


class _Scan:
    """phase 2 over one statement: words, operators, blocks, IF and FOR"""

    def __init__(self, text: str):
        self.text, self.at, self.open = text, 0, False

    def peek(self, n: int = 1) -> str:
        return self.text[self.at:self.at + n]

    def blank(self, newlines: bool = False) -> None:
        skip = " \t\n" if newlines else " \t"
        while self.at < len(self.text) and self.text[self.at] in skip:
            self.at += 1

    def seq(self, depth: int, line: bool = False) -> Seq:
        items, op = [], ""
        while True:
            self.blank(newlines=not line)
            if self.at >= len(self.text) or (line and self.peek() == "\n"):
                break
            if self.peek() == ")" and depth > 0:
                break
            if self.peek() == ":" and depth > 0 and not line:
                # a label or a :: comment inside a block is the rest of its line
                end = self.text.find("\n", self.at)
                self.at = len(self.text) if end < 0 else end + 1
                continue
            pipe = self.pipe(depth)
            if pipe is not None:
                items.append((op, pipe))
            op = ""
            self.blank()
            two = self.peek(2)
            if two in ("&&", "||"):
                op = two
                self.at += 2
            elif two[:1] == "&":
                op = "&"
                self.at += 1
            elif two[:1] == "\n":
                if line:
                    break
                self.at += 1
            elif two[:1] == ")" and depth > 0:
                break
            elif not two:
                break
            else:
                # a stray ) at the top level, which cmd treats as a word
                self.at += 1
        return Seq(items)

    def pipe(self, depth: int):
        commands = []
        while True:
            command = self.command(depth)
            if command is not None:
                commands.append(command)
            self.blank()
            if self.peek() == "|" and self.peek(2) != "||":
                self.at += 1
                continue
            break
        return Pipe(commands) if commands else None

    def command(self, depth: int):
        self.blank()
        while self.peek() == "@":
            self.at += 1
            self.blank()
        if self.peek() == "(":
            self.at += 1
            body = self.seq(depth + 1)
            if self.peek() == ")":
                self.at += 1
            else:
                self.open = True
            return Block(body, self.redirects())
        verb = self.first_word().lower()
        if verb == "rem" or re.match(r"^rem[.:/\\]", verb):
            end = self.text.find("\n", self.at)
            self.at = len(self.text) if end < 0 else end
            return None
        if verb == "if" or verb.startswith("if/"):
            self.at += 2
            return self.if_(depth)
        if verb == "for":
            self.at += 3
            return self.for_(depth)
        return self.simple(depth)

    def first_word(self) -> str:
        """the command word ahead, unconsumed, to tell IF, FOR and REM by"""
        at, out = self.at, []
        while at < len(self.text) and self.text[at] not in " \t\n&|<>()\"":
            out.append(self.text[at])
            at += 1
        return "".join(out)

    def body(self, depth: int):
        """what IF and FOR run: a block, or the rest of the line, & and all"""
        self.blank()
        if self.peek() == "(":
            return self.command(depth)
        return Block(self.seq(depth, line=True))

    def simple(self, depth: int) -> Simple:
        words, tail, redirects = [], [], []
        current, started, quoted = [], False, False

        def end_word():
            nonlocal current, started
            if started:
                words.append("".join(current))
            current, started = [], False

        while self.at < len(self.text):
            c = self.text[self.at]
            if quoted:
                current.append(c)
                if words:
                    tail.append(c)
                if c == '"':
                    quoted = False
                self.at += 1
                continue
            if c == '"':
                quoted = started = True
                current.append(c)
                if words:
                    tail.append(c)
                self.at += 1
                continue
            if c == "^" and self.at + 1 < len(self.text):
                nxt = self.text[self.at + 1]
                self.at += 2
                if nxt == "\n":
                    continue
                current.append(nxt)
                started = True
                if words:
                    tail.append(nxt)
                continue
            if c == "^":
                self.at += 1
                continue
            if c in "&|\n" or (c == ")" and depth > 0):
                break
            if c in "<>" or (c.isdigit() and not started and self.peek(2)[1:] in ("<", ">")):
                end_word()
                redirect = self.redirect()
                if redirect is not None:
                    redirects.append(redirect)
                continue
            if c in " \t":
                end_word()
                if words:
                    tail.append(c)
                self.at += 1
                continue
            current.append(c)
            started = True
            if words:
                tail.append(c)
            self.at += 1
        end_word()
        return Simple(words, "".join(tail).rstrip("\n") if words else "", redirects)

    def redirect(self):
        handle = None
        if self.peek().isdigit():
            handle = int(self.peek())
            self.at += 1
        c = self.peek()
        if c == "<":
            mode, self.at = "<", self.at + 1
            handle = 0 if handle is None else handle
        elif self.peek(2) == ">>":
            mode, self.at = ">>", self.at + 2
        else:
            mode, self.at = ">", self.at + 1
        handle = 1 if handle is None else handle
        if self.peek() == "&":
            self.at += 1
            target = ""
            while self.peek().isdigit():
                target += self.peek()
                self.at += 1
            return Redirect(handle, mode[0] + "&", target)
        self.blank()
        out, quoted = [], False
        while self.at < len(self.text):
            c = self.text[self.at]
            if c == '"':
                quoted = not quoted
            elif not quoted and (c in " \t\n&|<>" or c == ")"):
                break
            elif not quoted and c == "^" and self.at + 1 < len(self.text):
                self.at += 1
                c = self.text[self.at]
            out.append(c)
            self.at += 1
        return Redirect(handle, mode, "".join(out))

    def redirects(self) -> list:
        out = []
        while True:
            self.blank()
            c = self.peek()
            if c and (c in "<>" or (c.isdigit() and self.peek(2)[1:] in ("<", ">"))):
                out.append(self.redirect())
                continue
            return out

    def token(self) -> str:
        """a word for IF and FOR to read their own way: quotes kept, carets taken"""
        self.blank()
        out, quoted = [], False
        while self.at < len(self.text):
            c = self.text[self.at]
            if c == '"':
                quoted = not quoted
            elif not quoted and c in " \t\n":
                break
            elif not quoted and c == "^" and self.at + 1 < len(self.text):
                self.at += 1
                c = self.text[self.at]
            out.append(c)
            self.at += 1
        return "".join(out)

    def if_(self, depth: int) -> If:
        negate = fold = False
        while True:
            save = self.at
            word = self.token().lower()
            if word == "/i":
                fold = True
            elif word == "not":
                negate = True
            else:
                self.at = save
                break
        save = self.at
        word = self.token().lower()
        if word in ("errorlevel", "exist", "defined", "cmdextversion"):
            argument = self.token()
            if not argument:
                raise Syntax("The syntax of the command is incorrect.")
            kind, args = word, [argument]
        else:
            self.at = save
            kind, args = "compare", list(self.compare())
        then = self.body(depth)
        other = None
        if isinstance(then, Block) and isinstance(then.body, Seq) and self._else():
            other = self.body(depth)
        return If(negate, fold, kind, args, then, other)

    def _else(self) -> bool:
        save = self.at
        self.blank()
        if self.peek(4).lower() == "else" and self.text[self.at + 4:self.at + 5] in ("", " ", "\t",
                                                                                    "(", "\n"):
            self.at += 4
            return True
        self.at = save
        return False

    def compare(self) -> tuple:
        self.blank()
        left, quoted = [], False
        while self.at < len(self.text):
            c = self.text[self.at]
            if c == '"':
                quoted = not quoted
            elif not quoted:
                if self.text.startswith("==", self.at) or c in " \t\n":
                    break
                if c == "^" and self.at + 1 < len(self.text):
                    self.at += 1
                    c = self.text[self.at]
            left.append(c)
            self.at += 1
        self.blank()
        if self.text.startswith("==", self.at):
            self.at += 2
            while self.text.startswith("=", self.at):
                self.at += 1
            op = "=="
        else:
            word = self.token()
            op = word.upper()
            if op not in ("EQU", "NEQ", "LSS", "LEQ", "GTR", "GEQ"):
                raise Syntax(f"{word or 'what'} was unexpected at this time.")
        right = self.token()
        if not left or not right:
            raise Syntax("The syntax of the command is incorrect.")
        return "".join(left), op, right

    def for_(self, depth: int) -> For:
        switch, root, options = "", "", ""
        while True:
            save = self.at
            word = self.token().lower()
            if word in ("/d", "/l", "/f", "/r"):
                letter = word[1]
                switch = "dr" if {switch, letter} == {"d", "r"} else letter
                if letter in "rf":
                    save = self.at
                    candidate = self.token()
                    if letter == "r" and not candidate.startswith("%") and \
                            candidate.lower() != "/d":
                        root = unquote(candidate)
                    elif letter == "f" and candidate.startswith('"'):
                        options = candidate[1:-1] if candidate.endswith('"') else candidate[1:]
                    else:
                        self.at = save
                continue
            self.at = save
            break
        var = self.token()
        if not (len(var) == 2 and var[0] == "%") or self.token().lower() != "in":
            raise Syntax(f"{var} was unexpected at this time.")
        self.blank()
        if self.peek() != "(":
            raise Syntax("The syntax of the command is incorrect.")
        self.at += 1
        items, quote, quoted = [], "", False
        while self.at < len(self.text):
            c = self.text[self.at]
            if c == "^" and not quoted and self.at + 1 < len(self.text):
                # phase 2 takes the ^ off 'reg query ... ^| find' here, so the
                # command it runs gets a pipe. cmd knows no ' quotes, only "
                items.append(self.text[self.at + 1])
                self.at += 2
                continue
            if c == '"':
                quoted = not quoted
            if quote:
                if c == quote:
                    quote = ""
            elif c in "\"'`":
                quote = c
            elif c == ")":
                break
            items.append(c)
            self.at += 1
        if self.at >= len(self.text):
            self.open = True
        self.at += 1
        if self.token().lower() != "do":
            if self.at >= len(self.text):
                self.open = True
                return For(switch, root, options, var[1], "", Block(Seq([])))
            raise Syntax("The syntax of the command is incorrect.")
        body = self.body(depth)
        return For(switch, root, options, var[1], "".join(items).replace("\n", " "), body)


def parse(text: str) -> tuple:
    """(the statement, whether a ( in it is still open at the end)"""
    scan = _Scan(text)
    seq = scan.seq(0)
    return seq, scan.open


class Out:
    """a buffer a command's output goes into, read back after"""

    def __init__(self):
        self.parts = []

    def write(self, text: str) -> None:
        self.parts.append(text)

    def text(self) -> str:
        return "".join(self.parts)


class _Goto(Exception):
    def __init__(self, label: str):
        self.label = label


class _Exit(Exception):
    def __init__(self, code: int, whole: bool):
        self.code, self.whole = code, whole


class _Fail(Exception):
    pass


@dataclass
class Frame:
    script: Script
    pos: int
    args: list
    depth: int       # how many setlocals were open when it started
    label: str = ""
    every: str = ""  # %*, which shift never touches


@dataclass
class Io:
    stdin: object    # text piped or redirected in, or None for the person at the keyboard
    out: object
    err: object


class _Console:
    def __init__(self, run):
        self.run = run

    def write(self, text: str) -> None:
        self.run.say(text)


class _Sink:
    """a redirection into a file, created as cmd creates it before the command runs"""

    def __init__(self, run, target: str, append: bool):
        self.run, self.parts, self.done, self.append = run, [], False, append
        self.null = target.lower().rstrip(":") in ("nul", "con", "")
        self.path = None if self.null else join(run.cwd, target)
        if self.path is not None:
            if run.world.kind(parent(self.path)) != "dir":
                raise _Fail("The system cannot find the path specified.")
            if run.world.kind(self.path) == "dir":
                raise _Fail("Access is denied.")
            if not append or run.world.kind(self.path) is None:
                run.world.write(self.path, b"")

    def write(self, text: str) -> None:
        if not self.null:
            self.parts.append(text)

    def close(self) -> None:
        if self.done or self.null:
            return
        self.done = True
        data = encode("".join(self.parts).replace("\r\n", "\n").replace("\n", "\r\n"))
        if not data:
            return
        if self.append or self.run.world.kind(self.path) != "file":
            self.run.world.write(self.path, data, append=True)
            return
        # > writes from the start of the file whatever else wrote to it since,
        # the way ff13tool's own log.txt and a script's > log.txt share one
        before = self.run.world.read(self.path)
        self.run.world.write(self.path, data + before[len(data):])


class Run:
    """
    one play of a script. `world` is everything outside cmd itself, the files
    and the programs and whoever answers its questions (sandbox.World is the
    real one), and `env` is what the script inherits
    """

    def __init__(self, world, env: dict, cwd: str, limit: int | None = None, check=None):
        self.world, self.check = world, check
        self.env = {k.upper(): (k, v) for k, v in env.items()}
        self.cwd = norm(cwd)
        self.errorlevel = 0
        self.delayed = False
        self.locals = []
        self.frames = []
        self.dirs = []
        self.console = []
        # lines said in all, and how many of them were before the last question
        self.said, self.asked_at = 0, 0
        # every check it made and program it ran, and the line it's on, for
        # reading back why a play that changes nothing gave up. `unargued` is
        # the script reading a %1 nothing was dragged onto it for
        self.trace, self.line, self.last, self.noted = [], "", None, 0
        # the check a branch that leaves is being run for, and the one _test noted last
        self.branch, self.tested = None, None
        # a ( block's lines as written, and how far down it the last step was
        self.lines, self.at = [], 0
        self.unargued = False
        self.spins, self.looping, self.varied, self.spun = {}, 0, 0, None
        # every variable it's changed, by name, and which call a check's in:
        # a label a FOR calls once an item is a new one each time
        self.named, self.instance, self.calls = set(), 0, 0
        self.echo = True
        self.steps = 0
        self.limit = LIMIT if limit is None else limit
        self.seed = 12345

    def say(self, text: str) -> None:
        lines = text.replace("\r\n", "\n").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        self.console += lines
        self.said += len(lines)
        del self.console[:-400]

    def tail(self, n: int = 12) -> list:
        return [line for line in self.console if line.strip()][-n:]

    def note(self, step: Step) -> None:
        step.depth = len(self.frames) or 1
        step.within = self.branch
        if len(self.lines) > 1:
            step.line = self._line_of(step)
        self.trace.append(step)
        self.noted += 1
        if step.kind in ("exist", "defined") and not self.looping:
            same = (step.kind, str(step.what).lower(), step.ok, step.line, self.instance)
            # what's been changed since is part of it: a loop that copies a
            # piece a turn, or counts its tries, is getting somewhere
            still = same + (getattr(self.world, "changed", 0), self.varied)
            fresh = same + (getattr(self.world, "fresh", 0), len(self.named))
            for seen, most in ((still, SPIN), (fresh, SPUN_ANYWAY)):
                self.spins[seen] = self.spins.get(seen, 0) + 1
                if self.spins[seen] > most:
                    # the check it went round on is what it was waiting for
                    self.spun = step
                    raise Endless(SPUN)
            if len(self.spins) > 50_000:
                self.spins.clear()
        if len(self.trace) > 4000:
            # a loop's checks thousands deep: where it ended is what says why
            del self.trace[:2000]

    def _ran(self, name: str, text: str, code: int, how: str, io: Io) -> None:
        self.last = Ran(name, text, code, how, (io.stdin or "")[:400], cwd=self.cwd)
        self.note(Step("ran", self.last, code == 0, line=self.line))

    def called(self, name: str, text: str) -> bool:
        """
        a call back: what && || and errorlevel read next is its own end, not
        whatever ran last inside it, and a compare on %errorlevel% finds it
        """
        self.last = Ran(name, text, self.errorlevel, "call")
        self.note(Step("ran", self.last, self.errorlevel == 0, line=self.line))
        return self.errorlevel == 0

    def get(self, name: str):
        upper = name.upper()
        if upper in self.env:
            return self.env[upper][1]
        if upper == "CD":
            return self.cwd
        if upper == "__CD__":
            return self.cwd.rstrip("\\") + "\\"
        if upper == "ERRORLEVEL":
            return str(self.errorlevel)
        if upper == "RANDOM":
            self.seed = (self.seed * 1103515245 + 12345) & 0x7FFFFFFF
            return str(self.seed >> 16 & 0x7FFF)
        if upper == "DATE":
            return "Sat 01/01/2000"
        if upper == "TIME":
            return "12:00:00.00"
        if upper == "CMDEXTVERSION":
            return "2"
        if upper == "CMDCMDLINE":
            return 'C:\\Windows\\system32\\cmd.exe /c ""' + (
                self.frames[0].script.path if self.frames else "") + '" "'
        return None

    def put(self, name: str, value) -> None:
        was = self.env.get(name.upper(), (None, None))[1]
        if not value:
            self.env.pop(name.upper(), None)
        else:
            self.env[name.upper()] = (name, value)
        if (value or None) != was:
            self.varied += 1
            self.named.add(name.upper())

    def percent(self, text: str) -> str:
        """phase 1"""
        out, n = [], 0
        frame = self.frames[-1] if self.frames else None
        while n < len(text):
            c = text[n]
            if c != "%":
                out.append(c)
                n += 1
                continue
            nxt = text[n + 1:n + 2]
            if nxt == "%":
                out.append("%")
                n += 2
                continue
            if frame is not None and nxt == "*":
                out.append(frame.every)
                n += 2
                continue
            if frame is not None and nxt.isdigit():
                index = int(nxt)
                out.append(frame.args[index] if index < len(frame.args) else "")
                self._argued(index)
                n += 2
                continue
            if frame is not None and nxt == "~":
                hit = re.match(r"~([fdpnxsatz]*)(\$[^:]*:)?([0-9])", text[n + 1:], re.I)
                if hit:
                    index = int(hit.group(3))
                    value = frame.args[index] if index < len(frame.args) else ""
                    out.append(self.modify(value, hit.group(1).lower(), hit.group(2)))
                    self._argued(index)
                    n += 1 + hit.end()
                    continue
            close = text.find("%", n + 1)
            if close < 0:
                n += 1
                continue
            name = text[n + 1:close]
            base, colon, how = name.partition(":")
            value = self.get(base) if base and "\n" not in base else None
            if value is None:
                if colon and "\n" not in base:
                    # %undefined:~1% loses "%undefined:" and the scan goes on
                    n += 2 + len(base)
                    continue
                n = close + 1 if "\n" not in name else n + 1
                continue
            out.append(self.edit(value, how) if colon else value)
            n = close + 1
        return "".join(out)

    def _argued(self, index: int) -> None:
        if index and len(self.frames) == 1 and index >= len(self.frames[0].args):
            self.unargued = True

    def edit(self, value: str, how: str) -> str:
        """%var:~a,b% and %var:old=new%"""
        if how.startswith("~"):
            numbers = how[1:].split(",")
            try:
                start = _number(numbers[0]) if numbers[0].strip() else 0
                length = _number(numbers[1]) if len(numbers) > 1 else None
            except ValueError:
                return value
            if start < 0:
                start = max(0, len(value) + start)
            start = min(start, len(value))
            if length is None:
                return value[start:]
            end = len(value) + length if length < 0 else start + length
            return value[start:max(start, end)]
        if "=" in how:
            old, new = how.split("=", 1)
            if not old or old == "*":
                return value
            if old.startswith("*"):
                found = value.lower().find(old[1:].lower())
                return value if found < 0 else new + value[found + len(old) - 1:]
            return re.sub(re.escape(old), lambda _: new, value, flags=re.I)
        return value

    def modify(self, value: str, mods: str, search=None) -> str:
        """%~dp1 and the rest, on an argument or a FOR variable"""
        bare = value[1:-1] if len(value) > 1 and value[0] == value[-1] == '"' else unquote(value)
        if not mods and not search:
            return bare
        if not bare:
            return ""
        full = join(self.cwd, bare)
        if search:
            for folder in (self.get(search[1:-1]) or "").split(";"):
                if folder and self.world.kind(join(self.cwd, child(folder, bare))):
                    full = join(self.cwd, child(folder, bare))
                    break
            else:
                return ""
        full = self.world.spell(full)
        kind = self.world.kind(full)
        out = []
        if "a" in mods:
            out.append(("d--------" if kind == "dir" else "--a------") if kind else "")
        if "t" in mods:
            out.append("01/01/2000 12:00 PM" if kind else "")
        if "z" in mods:
            out.append(str(self.world.size(full)) if kind == "file" else "")
        if set(mods) & set("fdpnx"):
            name = leaf(full)
            folder = full[2:len(full) - len(name)] if name else "\\"
            stem, dot, ext = name.rpartition(".")
            stem, ext = (stem, "." + ext) if dot else (name, "")
            pieces = ""
            every = "f" in mods
            if every or "d" in mods:
                pieces += full[:2]
            if every or "p" in mods:
                pieces += folder
            if every or "n" in mods:
                pieces += stem
            if every or "x" in mods:
                pieces += ext
            out.append(pieces)
        shown = [piece for piece in out if piece]
        return " ".join(shown)

    def late(self, text: str, fors: dict) -> str:
        """FOR's variables, then !var!, both per command as it runs"""
        if fors and "%" in text:
            text = self._fors(text, fors)
        if self.delayed and "!" in text:
            text = self._bang(text)
        return text

    def _fors(self, text: str, fors: dict) -> str:
        out, n = [], 0
        while n < len(text):
            c = text[n]
            if c != "%":
                out.append(c)
                n += 1
                continue
            nxt = text[n + 1:n + 2]
            if nxt in fors:
                out.append(fors[nxt])
                n += 2
                continue
            if nxt == "~":
                hit = re.match(r"~([fdpnxsatz]*)(\$[^:]*:)?", text[n + 1:], re.I)
                mods, search = hit.group(1), hit.group(2)
                rest = n + 1 + hit.end()
                if text[rest:rest + 1] in fors and text[rest:rest + 1]:
                    out.append(self.modify(fors[text[rest]], mods.lower(), search))
                    n = rest + 1
                    continue
                # %~na with a FOR's %a: the variable's the last of the letters
                if mods and mods[-1] in fors and not search:
                    out.append(self.modify(fors[mods[-1]], mods[:-1].lower()))
                    n = rest
                    continue
            out.append(c)
            n += 1
        return "".join(out)

    def _bang(self, text: str) -> str:
        out, n = [], 0
        while n < len(text):
            c = text[n]
            if c == "^" and n + 1 < len(text):
                out.append(text[n + 1])
                n += 2
                continue
            if c != "!":
                out.append(c)
                n += 1
                continue
            close = text.find("!", n + 1)
            if close < 0:
                n += 1
                continue
            base, colon, how = text[n + 1:close].partition(":")
            value = self.get(base) if base else None
            if value is not None:
                out.append(self.edit(value, how) if colon else value)
            n = close + 1
        return "".join(out)

    def statement(self, frame: Frame):
        """
        the next statement from where the frame is, expanded and parsed, or
        None at its end. A ( left open pulls in lines until it's closed, and a
        ^ ending a line joins the next, the way cmd reads them
        """
        lines = frame.script.lines
        while frame.pos < len(lines):
            raw = lines[frame.pos]
            frame.pos += 1
            stripped = raw.lstrip(" \t@")
            if not stripped.strip() or stripped.startswith(":"):
                continue
            self.line = stripped.strip()
            self.lines, self.at = [self.line], 0
            text = self.percent(raw)
            while _continues(text) and not _remark(text) and frame.pos < len(lines):
                # the line break goes and the ^ escapes what the next line
                # starts with, so a long command reads as one line
                more = self.percent(lines[frame.pos])
                text = (text[:-1] if more[:1] in ("", " ", "\t") else text) + more
                frame.pos += 1
            seq, still_open = parse(text)
            while still_open and frame.pos < len(lines):
                self.lines.append(lines[frame.pos].strip(" \t@"))
                text += "\n" + self.percent(lines[frame.pos])
                frame.pos += 1
                seq, still_open = parse(text)
            return seq
        return None

    def _line_of(self, step: Step) -> str:
        """
        the line of a ( block a step was on, by what it checked or ran: the
        next one down that could be, the way a block runs, then any
        """
        if step.kind in ("exist", "copy"):
            want = leaf(str(step.what)).lower()
            keys = ("exist",) if step.kind == "exist" else ("copy", "move", "robocopy")
        elif step.kind == "defined":
            want, keys = str(step.what).lower(), ("defined",)
        elif step.kind == "compare":
            want, keys = "", ("==", " equ ", " neq ", " lss ", " leq ", " gtr ", " geq ")
        elif step.kind == "errorlevel":
            want, keys = "", ("errorlevel",)
        elif step.kind == "status":
            want, keys = "", ("||", "&&")
        elif step.kind == "ran" and isinstance(step.what, Ran):
            want, keys = leaf(step.what.name).lower().removesuffix(".exe"), ("",)
        else:
            return step.line
        for pool in (range(self.at, len(self.lines)), range(len(self.lines))):
            found = [n for n in pool if any(k in self.lines[n].lower() for k in keys)]
            if want:
                found = [n for n in found if want in self.lines[n].lower()] or found
            if found:
                self.at = found[0]
                return self.lines[found[0]]
        return step.line

    def run(self, script: Script, args: list) -> int:
        """plays a script through, its exit code back"""
        self.frames.append(Frame(script, 0, list(args), len(self.locals),
                                 every=" ".join(args[1:])))
        try:
            self._frame()
        except _Exit as e:
            self.errorlevel = e.code
        except RecursionError:
            # Python's own stack can run out first, a block or a FOR deep in
            # every call
            raise Endless(DEEPER) from None
        finally:
            self.frames.clear()
        return self.errorlevel

    def _frame(self) -> None:
        frame = self.frames[-1]
        try:
            while True:
                seq = self.statement(frame)
                if seq is None:
                    return
                try:
                    self.seq(seq, {})
                except _Goto as g:
                    self._jump(frame, g.label)
        finally:
            # leaving a script or a call ends the setlocals it started
            while len(self.locals) > frame.depth:
                self._endlocal()

    def _jump(self, frame: Frame, label: str) -> None:
        name = re.split(r"[ \t:+,;=]", label.lstrip(":").lstrip(" \t").lower() + " ", 1)[0]
        if name == "eof":
            frame.pos = len(frame.script.lines)
            return
        spots = frame.script.labels.get(name)
        if not spots:
            self.say(f"The system cannot find the batch label specified - {label}")
            # cmd gives up on the whole script there
            raise _Exit(1, True)
        after = [n for n in spots if n >= frame.pos]
        frame.pos = (after[0] if after else spots[0]) + 1

    def tick(self) -> None:
        self.steps += 1
        if self.check is not None and not self.steps % 512:
            # Stop in the drawer, between statements
            self.check(self.steps)
        if self.steps > self.limit:
            raise Endless("it ran millions of steps without finishing, so it's caught in a loop")

    def seq(self, seq: Seq, fors: dict, io: Io | None = None) -> bool:
        """whether the last command it ran worked, which is what && and || after a block read"""
        ok = True
        for op, pipe in seq.items:
            if (op == "&&" and not ok) or (op == "||" and ok):
                continue
            if op in ("&&", "||") and _leaves(pipe):
                # off on how the command before went: reg query ... || exit /b
                step = Step("status", self.last, ok, True, self.line)
                self.note(step)
                # a || is it giving up, a && its going on
                ok = self._branch(step if op == "||" else None,
                                  lambda: self.pipe(pipe, fors, io or Io(None, None, None)))
                continue
            ok = self.pipe(pipe, fors, io or Io(None, None, None))
        return ok

    def _branch(self, step, go, taken=None):
        """a check's way off run, everything on it noted as that check's"""
        held, mark = self.branch, self.noted
        # choice's errorlevel is the key pressed, not a failure
        failed = self.errorlevel != 0 and not (isinstance(self.last, Ran)
                                               and self.last.name.lower() == "choice")
        giving = step is not None and step.leaves and _gives_up(step, taken, failed)
        if giving:
            self.branch = step
        try:
            return go()
        finally:
            self.branch = held
            if giving:
                ran = self.trace[len(self.trace) - min(self.noted - mark, len(self.trace)):]
                if self._worked(ran):
                    # it went on to the install from there, "install it unless
                    # it's in already", so what it ran is work, not a goodbye
                    for each in ran:
                        if each.within is step:
                            each.within = None

    def _worked(self, steps) -> bool:
        """
        whether a branch went on to the install: a copy into the game that
        went, or a program handed one of the game's files or folders. Never a
        log kept on the way out, or a message box shown the readme or the script
        """
        in_game = getattr(self.world, "in_game", None)
        if in_game is None:
            return False
        for step in steps:
            if step.kind == "copy":
                if step.ok and in_game(str(step.what)):
                    return True
                continue
            ran = step.what if step.kind == "ran" else None
            if not isinstance(ran, Ran) or not (ran.how == "program" or ran.name.lower() in (
                    "xcopy", "robocopy", "powershell", "pwsh")):
                continue
            if any(word and not word.startswith(("/", "-")) and in_game(join(self.cwd, word))
                   and self.world.kind(join(self.cwd, word)) is not None
                   for word in split_args(ran.text)):
                return True
        return False

    def pipe(self, pipe: Pipe, fors: dict, io: Io) -> bool:
        if len(pipe.commands) == 1:
            return self.command(pipe.commands[0], fors, io)
        data, ok, feeding = io.stdin, True, None
        for n, command in enumerate(pipe.commands):
            last = n == len(pipe.commands) - 1
            buffer = None if last else Out()
            before = self.last
            ok = self.command(command, fors, Io(data, io.out if last else buffer, io.err))
            ran = self.last if self.last is not before else None
            if ran is not None and n:
                # a find that came up empty reads as the query that fed it
                ran.piped = feeding
            feeding = ran
            if buffer is not None:
                data = buffer.text()
        return ok

    def command(self, node, fors: dict, io: Io) -> bool:
        self.tick()
        if isinstance(node, Block):
            try:
                inner = self._redirected(node.redirects, fors, io)
            except _Fail as e:
                self.say(str(e))
                self.errorlevel = 1
                return False
            try:
                return self.seq(node.body, fors, inner)
            finally:
                _close(inner, io)
        if isinstance(node, If):
            taken = node.then if self._test(node, fors) else node.other
            if taken is None:
                return True
            return self._branch(self.tested, lambda: self.command(taken, fors, io), taken)
        if isinstance(node, For):
            self._for(node, fors, io)
            return True
        return self.simple(node, fors, io)

    def _redirected(self, redirects: list, fors: dict, io: Io) -> Io:
        if not redirects:
            return io
        stdin, out, err = io.stdin, io.out, io.err
        for r in redirects:
            if r.mode in (">&", "<&"):
                if r.handle == 2 and r.target == "1":
                    err = out
                elif r.handle == 1 and r.target == "2":
                    out = err
                continue
            target = unquote(self.late(r.target, fors))
            if r.mode == "<":
                stdin = self.text_of(target)
                if stdin is None:
                    raise _Fail("The system cannot find the file specified.")
                continue
            sink = _Sink(self, target, append=r.mode == ">>")
            if r.handle == 2:
                err = sink
            else:
                out = sink
        return Io(stdin, out, err)

    def text_of(self, path: str):
        """a file's text, or None when there's no such file"""
        if path.lower().rstrip(":") == "nul":
            return ""
        full = join(self.cwd, path)
        if self.world.kind(full) != "file":
            return None
        if self.world.size(full) > TEXT_LIMIT:
            return ""
        return decode(self.world.read(full))

    def simple(self, node: Simple, fors: dict, io: Io) -> bool:
        if not node.words:
            return True
        try:
            inner = self._redirected(node.redirects, fors, io)
        except _Fail as e:
            self.say(str(e))
            self.errorlevel = 1
            return False
        verb, text, here = None, "", inner
        try:
            words = [self.late(w, fors) for w in node.words]
            tail = self.late(node.tail, fors)
            here = Io(inner.stdin, inner.out if inner.out is not None else _Console(self),
                      inner.err if inner.err is not None else _Console(self))
            name = unquote(words[0])
            verb, glued = _verb(name)
            text = tail[1:] if tail[:1] in (" ", "\t") else tail
            if verb is None:
                return self.external(name, text, here)
            if glued and verb == "echo":
                # echo. echo: echo( all print what follows that one character
                here.out.write(glued[1:] + tail + "\n")
                return True
            ok = BUILTINS[verb](self, words[1:], text, here) if not glued else \
                BUILTINS[verb](self, [glued] + words[1:], glued + tail, here)
            if verb in WATCHED:
                self._builtin(verb, text, ok, here)
            return ok
        except _Fail as e:
            self.say(str(e))
            if self.errorlevel == 0:
                self.errorlevel = 1
            if verb in WATCHED:
                self._builtin(verb, text, False, here)
            return False
        finally:
            _close(inner, io)

    def _builtin(self, verb: str, text: str, ok: bool, io: Io) -> None:
        """one of cmd's own that can fail on a path, for what && || and errorlevel read"""
        if ok:
            self.last = Ran(verb, text, 0, "builtin")
        else:
            self._ran(verb, text, self.errorlevel or 1, "builtin", io)

    def _test(self, node: If, fors: dict) -> bool:
        self.tested = None
        args = [self.late(a, fors) for a in node.args]
        what, sets = None, ""
        if node.kind == "errorlevel":
            try:
                result = self.errorlevel >= _number(args[0])
            except ValueError:
                result = False
            what = self.last
        elif node.kind == "exist":
            asked = unquote(args[0])
            result = self.exists(asked)
            what = join(self.cwd, asked) if asked else ""
            # a find that sets a variable is a search: a miss there isn't
            # giving up while another place still might set it
            hit = node.other if node.negate else node.then
            sets = _sets(hit) if hit is not None else ""
        elif node.kind == "defined":
            result = self.get(args[0]) is not None
            what = args[0]
        elif node.kind == "cmdextversion":
            result = 2 >= _number(args[0] or "0")
        else:
            result = _compare(*args, node.fold)
            what = (args[0], args[2]) if len(args) > 2 else tuple(args)
        final = result != node.negate
        if node.kind != "cmdextversion" and what != "":
            taken = node.then if final else node.other
            self.tested = Step(node.kind, what, result, taken is not None and _leaves(taken),
                               self.line, sets)
            self.note(self.tested)
        return final

    def exists(self, path: str) -> bool:
        if not path:
            return False
        if path.lower().rstrip(":") in ("nul", "con"):
            return True
        full = join(self.cwd, path)
        name = leaf(full)
        if wild(name):
            folder = parent(full)
            return self.world.kind(folder) == "dir" and any(
                matches(n, name) for n, _ in self.world.listdir(folder))
        kind = self.world.kind(full)
        if path.rstrip(" ").endswith(("\\", "/")):
            return kind == "dir"
        return kind is not None

    def _for(self, node: For, fors: dict, io: Io) -> None:
        items = self.late(node.items, fors)
        root = self.late(node.root, fors)
        # a check a FOR repeats once per item is it going through its list, not a wait
        self.looping += 1
        try:
            for value in self._values(node, items, root):
                self.tick()
                inner = dict(fors)
                if isinstance(value, tuple):
                    for letter, piece in zip(_letters(node.var, len(value)), value):
                        inner[letter] = piece
                else:
                    inner[node.var] = value
                self.command(node.body, inner, io)
        finally:
            self.looping -= 1

    def _values(self, node: For, items: str, root: str):
        if node.switch == "l":
            numbers = [_number(x) for x in re.split(r"[ ,;=\t]+", items.strip()) if x]
            start, step, end = (numbers + [0, 0, 0])[:3]
            if step == 0:
                return
            n = start
            while (step > 0 and n <= end) or (step < 0 and n >= end):
                yield str(n)
                n += step
            return
        if node.switch == "f":
            yield from self._lines_of(node, items)
            return
        words = batch_args(items)
        if node.switch in ("r", "dr"):
            base = join(self.cwd, root) if root else self.cwd
            if self.world.kind(base) != "dir":
                return
            for folder in list(self.tree(base)):
                for word in words:
                    bare = unquote(word)
                    if node.switch == "dr" or wild(bare):
                        for name, is_dir in self.listing(folder):
                            if is_dir == (node.switch == "dr") and matches(name, bare):
                                yield child(folder, name)
                    else:
                        # (.) is every folder, as C:\mod\sub\. the way cmd spells it
                        yield child(folder, bare)
            return
        for word in words:
            bare = unquote(word)
            if not wild(bare):
                yield word
                continue
            full = join(self.cwd, bare)
            folder, pattern = parent(full), leaf(full)
            if self.world.kind(folder) != "dir":
                continue
            spelled = bare.replace("/", "\\")
            prefix = spelled[:spelled.rfind("\\") + 1]
            for name, is_dir in self.listing(folder):
                if is_dir == (node.switch == "d") and matches(name, pattern):
                    yield prefix + name

    def listing(self, folder: str) -> list:
        return sorted(self.world.listdir(folder), key=lambda item: item[0].lower())

    def tree(self, base: str):
        yield base
        for name, is_dir in self.listing(base):
            if is_dir and name not in ("", ".", ".."):
                yield from self.tree(child(base, name))

    def _lines_of(self, node: For, items: str):
        options = _for_options(node.options)
        source, back, texts = items.strip(), options["usebackq"], []
        if source[:1] == ("`" if back else "'"):
            texts.append(self.capture(source[1:-1] if len(source) > 1 and source[-1] == source[0]
                                      else source[1:]))
        elif source[:1] == ("'" if back else '"'):
            texts.append(source[1:-1] if len(source) > 1 and source[-1] == source[0]
                         else source[1:])
        else:
            for name in (_names(source) if back else batch_args(source)):
                text = self.text_of(unquote(name))
                if text is None:
                    self.say(f"The system cannot find the file {unquote(name)}.")
                    self.errorlevel = 1
                    return
                texts.append(text)
        skip = options["skip"]
        for text in texts:
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                if skip:
                    skip -= 1
                    continue
                if not line or (options["eol"] and line.startswith(options["eol"])):
                    continue
                values = _tokens(line, options)
                if values is not None:
                    yield values

    def capture(self, command: str) -> str:
        """what a command prints, for FOR /F's 'command'"""
        buffer = Out()
        stripped = command.strip()
        if stripped.startswith('""') and stripped.count('"') > 2:
            # cmd /c takes off the first quote and the last, which is why a
            # quoted program with quoted arguments is wrapped in one more pair
            end = stripped.rindex('"')
            command = stripped[1:end] + stripped[end + 1:]
        seq, _ = parse(command)
        cwd = self.cwd
        try:
            self.seq(seq, {}, Io(None, buffer, Out()))
        finally:
            self.cwd = cwd
        return buffer.text()

    def external(self, name: str, text: str, io: Io) -> bool:
        found = self.find_program(name)
        if found is None:
            bare = re.sub(r"\.(exe|com)$", "", name.lower())
            # one of Windows' own by its full path, "%SYSTEMROOT%\system32\cacls.exe",
            # the way the admin check every script copies calls it
            windows = self.get("SystemRoot") or self.get("windir") or "C:\\Windows"
            if re.search(r"[\\/]", bare) and key(parent(join(self.cwd, bare))) in {
                    key(child(windows, d)) for d in ("System32", "SysWOW64", "Sysnative")}:
                bare = leaf(bare)
            code = SYSTEM[bare](self, text, io) if bare in SYSTEM else \
                self.world.tool(bare, text, self.cwd, io)
            if code is None:
                io.err.write(f"'{name}' is not recognized as an internal or external command,\n"
                             "operable program or batch file.\n")
                self.errorlevel = NOT_FOUND
                self._ran(name, text, NOT_FOUND, "missing", io)
                return False
            self.errorlevel = code
            self._ran(bare, text, code, "cmd" if bare in SYSTEM else "pc", io)
            return code == 0
        if found.lower().endswith((".bat", ".cmd")):
            # a .bat started without CALL takes over from this one for good
            self.script_call(found, text)
            raise _Exit(self.errorlevel, False)
        self.errorlevel = self.world.program(found, text, self.cwd, io)
        self._ran(found, text, self.errorlevel, "program", io)
        return self.errorlevel == 0

    def find_program(self, name: str):
        """where cmd finds a program by this name: the folder it's in, then PATH"""
        # a stand-in of the world's own goes first, theirs shipped or not
        builtin = getattr(self.world, "builtin", None)
        found = builtin(name) if builtin is not None and name else None
        if found:
            return found
        if not name or (re.sub(r"\.(exe|com)$", "", name.lower()) in SYSTEM
                        and not re.search(r"[\\/:]", name)):
            return None
        exts = [e.lower() for e in (self.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(";") if e]
        given = any(name.lower().endswith(e) for e in exts)
        folders = [""]
        if not re.search(r"[\\/:]", name):
            folders += [p for p in (self.get("PATH") or "").split(";") if p]
        for folder in folders:
            base = join(self.cwd, child(folder, name) if folder else name)
            for candidate in ([base] if given else []) + [base + e for e in exts]:
                if self.world.kind(candidate) == "file":
                    return self.world.spell(candidate)
        return None

    def script_call(self, path: str, text: str) -> None:
        script = self.world.script(path)
        self._call(Frame(script, 0, [path] + batch_args(text), len(self.locals),
                         every=text.strip(" \t")))

    def label_call(self, label: str, text: str) -> None:
        here = self.frames[-1]
        frame = Frame(here.script, here.pos, [label] + batch_args(text), len(self.locals), label,
                      every=text.strip(" \t"))
        name = re.split(r"[ \t:+,;=]", label.lstrip(":").lstrip(" \t").lower() + " ", 1)[0]
        if name != "eof" and name not in here.script.labels:
            # a call to a label that isn't there is said, and the script goes on
            self.say(f"The system cannot find the batch label specified - {label.lstrip(':')}")
            self.errorlevel = 1
            return
        self._jump(frame, label)
        self._call(frame)

    def _call(self, frame: Frame) -> None:
        if len(self.frames) > 200:
            raise Endless(DEEPER)
        self.frames.append(frame)
        # a call's own lines are its own, and a wait in a label a FOR calls
        # is still a wait; the caller's block picks up where it was after
        held = self.line, self.lines, self.at, self.looping, self.instance
        if self.looping:
            # one call an item: its checks are this item's, not the last one's
            self.calls += 1
            self.instance = self.calls
        self.looping = 0
        try:
            self._frame()
        except _Exit as e:
            if e.whole:
                raise
            self.errorlevel = e.code
        finally:
            self.frames.pop()
            self.line, self.lines, self.at, self.looping, self.instance = held

    def _endlocal(self) -> None:
        self.env, self.delayed, self.cwd = self.locals.pop()

    def path(self, word: str) -> str:
        return join(self.cwd, unquote(word))

    def glob(self, word: str, dirs: bool = False) -> list:
        """what a word names: itself, or every match of its wildcard"""
        full = self.path(word)
        name = leaf(full)
        if not wild(name):
            return [full]
        folder = parent(full)
        if self.world.kind(folder) != "dir":
            return []
        return [child(folder, n) for n, is_dir in self.listing(folder)
                if matches(n, name) and (dirs or not is_dir)]

    def ensure(self, folder: str) -> None:
        chain, at = [], folder
        while self.world.kind(at) is None and at != parent(at):
            chain.append(at)
            at = parent(at)
        for each in reversed(chain):
            self.world.mkdir(each)

    def ask(self, kind: str, prompt: str, options: str = "", var: str = "") -> str:
        frame = self.frames[-1] if self.frames else None
        where = (frame.script.path, frame.pos) if frame else ()
        calls = "/".join(f"{f.label}:{' '.join(f.args[1:])}" for f in self.frames)
        # what it put up for this question: a question before it, and what
        # was said above that, is that one's. A menu can be up from before
        since = self.console[len(self.console) - min(self.said - self.asked_at,
                                                     len(self.console)):]
        self.asked_at = self.said
        return self.world.answer(Ask(kind, prompt, options=options, var=var, where=where,
                                     lines=tuple(self.tail(16)), frame=calls,
                                     new=tuple([line for line in since if line.strip()][-16:])))


def _close(inner: Io, outer: Io) -> None:
    for sink in (inner.out, inner.err):
        if isinstance(sink, _Sink) and sink not in (outer.out, outer.err):
            sink.close()


def _remark(text: str) -> bool:
    """
    whether a line is or ends in a REM, whose ^ at the end is only more of
    the remark: one where a command starts, past quotes and escaped &s
    """
    quoted, n, starts = False, 0, [0]
    while n < len(text):
        c = text[n]
        if c == '"':
            quoted = not quoted
        elif c == "^" and not quoted:
            n += 2
            continue
        elif c in "&|(" and not quoted:
            starts.append(n + 1)
        n += 1
    return any(re.match(r"[\s@]*rem([\s.:/\\]|$)", text[at:], re.I) for at in starts)


def _continues(text: str) -> bool:
    """a line ending in an unescaped ^ outside quotes runs on into the next"""
    quoted, n = False, 0
    while n < len(text):
        c = text[n]
        if c == '"':
            quoted = not quoted
        elif c == "^" and not quoted:
            if n == len(text) - 1:
                return True
            n += 2
            continue
        n += 1
    return False


def _verb(word: str):
    """(the internal command a word is, what's glued on after it), (None, "") for none"""
    low = word.lower()
    if low in BUILTINS:
        return low, ""
    hit = re.match(r"^([a-z]+)([.:(\\/+=,;\[\]].*)$", low)
    if hit and hit.group(1) in GLUED:
        return hit.group(1), word[len(hit.group(1)):]
    return None, ""


def _number(text: str) -> int:
    text = text.strip()
    if re.fullmatch(r"[-+]?0x[0-9a-f]+", text, re.I):
        return int(text, 16)
    if re.fullmatch(r"[-+]?0[0-7]+", text):
        return int(text, 8)
    return int(text)


def _compare(left: str, op: str, right: str, fold: bool) -> bool:
    if op == "==":
        return left.lower() == right.lower() if fold else left == right
    try:
        a, b = _number(left), _number(right)
    except ValueError:
        a, b = (left.lower(), right.lower()) if fold else (left, right)
    return {"EQU": a == b, "NEQ": a != b, "LSS": a < b, "LEQ": a <= b,
            "GTR": a > b, "GEQ": a >= b}[op]


def _letters(first: str, count: int) -> list:
    return [chr(ord(first) + n) for n in range(count)]


def _for_options(text: str) -> dict:
    options = {"eol": ";", "skip": 0, "delims": " \t", "tokens": [1], "star": False,
               "usebackq": False}
    n = 0
    while n < len(text):
        rest = text[n:]
        low = rest.lower()
        if low.startswith("usebackq"):
            options["usebackq"] = True
            n += 8
        elif low.startswith("eol="):
            options["eol"] = rest[4:5]
            n += 5
        elif low.startswith("skip="):
            hit = re.match(r"skip=(\d+)", low)
            options["skip"] = int(hit.group(1)) if hit else 0
            n += hit.end() if hit else 5
        elif low.startswith("delims="):
            # to the end, or to a space before the next option
            hit = re.match(r"delims=(.*?)(?= (?:eol|skip|tokens|usebackq)\b|$)", rest, re.I)
            options["delims"] = hit.group(1)
            n += hit.end()
        elif low.startswith("tokens="):
            hit = re.match(r"tokens=([0-9,\-*]+)", low)
            spec = hit.group(1) if hit else "1"
            options["star"] = spec.endswith("*")
            picked = []
            for part in spec.rstrip("*").split(","):
                if "-" in part:
                    a, b = part.split("-", 1)
                    picked += range(int(a), int(b) + 1)
                elif part:
                    picked.append(int(part))
            options["tokens"] = sorted(set(picked))
            n += hit.end() if hit else 7
        else:
            n += 1
    return options


def _tokens(line: str, options: dict):
    delims = options["delims"]
    pieces, starts, n = [], [], 0
    if not delims:
        pieces, starts = [line], [0]
    else:
        while n < len(line):
            while n < len(line) and line[n] in delims:
                n += 1
            if n >= len(line):
                break
            starts.append(n)
            while n < len(line) and line[n] not in delims:
                n += 1
            pieces.append(line[starts[-1]:n])
    picked = options["tokens"]
    values = [pieces[p - 1] if p - 1 < len(pieces) else "" for p in picked]
    if options["star"]:
        after = picked[-1] if picked else 0
        values.append(line[starts[after]:] if after < len(pieces) else "")
    if not values or not values[0] and not any(values):
        return None
    return tuple(values)


def _names(source: str) -> list:
    """usebackq's file set: names in quotes may have spaces"""
    out, word, quoted = [], [], False
    for c in source:
        if c == '"':
            quoted = not quoted
            continue
        if c in " \t" and not quoted:
            if word:
                out.append("".join(word))
                word = []
            continue
        word.append(c)
    if word:
        out.append("".join(word))
    return out


class _Arith:
    """SET /A: cmd's integer arithmetic, 32-bit, assignments and all"""

    TOKEN = re.compile(r"\s*(0x[0-9a-fA-F]+|\d+|[^\s\d\-+*/%&|^~!(),=<>][^\s\-+*/%&|^~!(),=<>]*|"
                       r"<<=|>>=|\*=|/=|%=|\+=|-=|&=|\^=|\|=|<<|>>|[-+*/%&|^~!(),=])")
    ASSIGN = ("=", "*=", "/=", "%=", "+=", "-=", "&=", "^=", "|=", "<<=", ">>=")
    LEVELS = [("|",), ("^",), ("&",), ("<<", ">>"), ("+", "-"), ("*", "/", "%")]

    def __init__(self, run: Run, text: str):
        self.run, self.tokens, self.n = run, [], 0
        text, at = text.replace('"', " "), 0
        while at < len(text):
            if text[at] in " \t":
                at += 1
                continue
            hit = self.TOKEN.match(text, at)
            if not hit:
                raise _Fail("Missing operator.")
            self.tokens.append(hit.group(1))
            at = hit.end()

    def peek(self):
        return self.tokens[self.n] if self.n < len(self.tokens) else None

    def take(self):
        token = self.peek()
        self.n += 1
        return token

    def all(self) -> int:
        value = 0
        while self.peek() is not None:
            value = self.assign()
            if self.peek() == ",":
                self.take()
            elif self.peek() is not None:
                raise _Fail("Missing operator.")
        return value

    def assign(self) -> int:
        if self.n + 1 < len(self.tokens) and _is_name(self.tokens[self.n]) and \
                self.tokens[self.n + 1] in self.ASSIGN:
            name, op = self.take(), self.take()
            value = self.assign()
            if op != "=":
                value = _apply(op[:-1], _int(self.run.get(name) or "0"), value)
            value = _wrap(value)
            self.run.put(name, str(value))
            return value
        return self.binary(0)

    def binary(self, level: int) -> int:
        if level == len(self.LEVELS):
            return self.unary()
        value = self.binary(level + 1)
        while self.peek() in self.LEVELS[level]:
            op = self.take()
            value = _wrap(_apply(op, value, self.binary(level + 1)))
        return value

    def unary(self) -> int:
        token = self.peek()
        if token in ("-", "+", "!", "~"):
            self.take()
            value = self.unary()
            return _wrap({"-": -value, "+": value, "!": int(not value), "~": ~value}[token])
        if token == "(":
            self.take()
            value = self.assign()
            while self.peek() == ",":
                self.take()
                value = self.assign()
            if self.take() != ")":
                raise _Fail("Missing operand.")
            return value
        token = self.take()
        if token is None:
            raise _Fail("Missing operand.")
        return _int(self.run.get(token) or "0") if _is_name(token) else _int(token)


def _is_name(token) -> bool:
    return bool(token) and not token[0].isdigit() and token not in (
        "-", "+", "*", "/", "%", "&", "|", "^", "~", "!", "(", ")", ",", "=", "<<", ">>")


def _int(text: str) -> int:
    text = text.strip()
    try:
        if re.fullmatch(r"-?0x[0-9a-f]+", text, re.I):
            value = int(text, 16)
        elif re.fullmatch(r"-?0[0-7]+", text):
            value = int(text, 8)
        else:
            hit = re.match(r"^-?\d+", text)
            value = int(hit.group(0)) if hit else 0
    except ValueError:
        value = 0
    return _wrap(value)


def _wrap(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


def _apply(op: str, a: int, b: int) -> int:
    if op in ("/", "%") and b == 0:
        raise _Fail("Divide by zero error.")
    if op == "/":
        return int(a / b)
    if op == "%":
        return a - int(a / b) * b
    return {"+": a + b, "-": a - b, "*": a * b, "&": a & b, "|": a | b, "^": a ^ b,
            "<<": a << (b & 31), ">>": a >> (b & 31)}[op]


def _echo(run: Run, words: list, text: str, io: Io) -> bool:
    if not text.strip(" \t"):
        io.out.write(f"ECHO is {'on' if run.echo else 'off'}.\n")
        return True
    if text.strip(" \t").lower() in ("on", "off"):
        run.echo = text.strip(" \t").lower() == "on"
        return True
    io.out.write(text + "\n")
    return True


def _set(run: Run, words: list, text: str, io: Io) -> bool:
    body = text.lstrip(" \t")
    low = body.lower()
    if low.startswith("/a"):
        try:
            _Arith(run, body[2:]).all()
        except _Fail as e:
            io.err.write(f"{e}\n")
            run.errorlevel = 1073750993 if "zero" in str(e) else 9165
            return False
        run.errorlevel = 0
        return True
    if low.startswith("/p"):
        name, prompt = _assignment(body[2:].lstrip(" \t"))
        if prompt.startswith('"') and '"' in prompt[1:]:
            # set /p strips the quotes a prompt's wrapped in
            prompt = prompt[1:prompt.rfind('"')]
        if not name:
            # set /p ="." with no name: a line with no newline, a row of dots
            io.out.write(prompt)
            return True
        if io.stdin is not None:
            first = io.stdin.replace("\r\n", "\n").split("\n")[0]
            if not io.stdin:
                run.errorlevel = 1
                return False
            run.put(name, first)
            return True
        io.out.write(prompt)
        answer = run.ask("line", prompt, var=name)
        if answer == "":
            # Enter alone leaves the variable be, and errorlevel says so
            run.errorlevel = 1
            return False
        run.put(name, answer)
        return True
    if "=" not in body:
        prefix = unquote(body).strip().upper()
        shown = sorted((k for k in run.env if k.startswith(prefix)), key=str.lower)
        for k in shown:
            io.out.write(f"{run.env[k][0]}={run.env[k][1]}\n")
        if not shown:
            io.err.write(f"Environment variable {prefix} not defined\n")
            run.errorlevel = 1
            return False
        return True
    name, value = _assignment(body)
    if not name:
        io.err.write("The syntax of the command is incorrect.\n")
        run.errorlevel = 1
        return False
    run.put(name, value)
    return True


def _assignment(body: str) -> tuple:
    """(name, value) out of set's argument, quoted the cmd way or not"""
    if body.startswith('"'):
        last = body.rfind('"')
        body = body[1:last] if last > 0 else body[1:]
    name, _, value = body.partition("=")
    return name, value


def _setlocal(run: Run, words: list, text: str, io: Io) -> bool:
    run.locals.append((dict(run.env), run.delayed, run.cwd))
    for word in (unquote(w).lower() for w in words):
        if word == "enabledelayedexpansion":
            run.delayed = True
        elif word == "disabledelayedexpansion":
            run.delayed = False
    return True


def _endlocal(run: Run, words: list, text: str, io: Io) -> bool:
    frame = run.frames[-1] if run.frames else None
    if run.locals and (frame is None or len(run.locals) > frame.depth):
        run._endlocal()
    return True


def _goto(run: Run, words: list, text: str, io: Io) -> bool:
    label = unquote(words[0]) if words else ""
    if not label.strip(":"):
        raise _Fail("No batch label specified to GOTO command.")
    raise _Goto(label)


def _call(run: Run, words: list, text: str, io: Io) -> bool:
    if not words:
        return True
    first = unquote(words[0])
    rest = text.lstrip(" \t")[len(words[0]):]
    rest = rest[1:] if rest[:1] in (" ", "\t", ",", ";", "=") else rest
    if first.startswith(":"):
        run.label_call(first, rest)
        return run.called(first, rest)
    found = run.find_program(first)
    if found is not None and found.lower().endswith((".bat", ".cmd")):
        run.script_call(found, rest)
        return run.called(first, rest)
    # call on anything else takes the % off once more, then runs it
    seq, _ = parse(run.percent(text))
    run.seq(seq, {}, io)
    return run.errorlevel == 0


def _exit(run: Run, words: list, text: str, io: Io) -> bool:
    plain = [unquote(w) for w in words]
    whole = not (plain and plain[0].lower() == "/b")
    if not whole:
        plain = plain[1:]
    code = run.errorlevel
    if plain:
        try:
            code = _number(plain[0])
        except ValueError:
            code = 0
    raise _Exit(code, whole)


def _shift(run: Run, words: list, text: str, io: Io) -> bool:
    frame = run.frames[-1]
    start = 0
    if words and unquote(words[0]).startswith("/"):
        try:
            start = int(unquote(words[0])[1:])
        except ValueError:
            start = 0
    if start < len(frame.args):
        del frame.args[start]
    return True


def _cd(run: Run, words: list, text: str, io: Io) -> bool:
    target = text.strip(" \t")
    if target.lower().startswith("/d"):
        target = target[2:].strip(" \t")
    target = unquote(target).strip(" \t")
    if not target:
        io.out.write(run.cwd + "\n")
        return True
    full = run.path(target)
    if run.world.kind(full) != "dir":
        io.err.write("The system cannot find the path specified.\n")
        run.errorlevel = 1
        return False
    run.cwd = run.world.spell(full)
    run.errorlevel = 0
    return True


def _pushd(run: Run, words: list, text: str, io: Io) -> bool:
    target = unquote(text.strip(" \t"))
    if not target:
        return True
    full = run.path(target)
    if run.world.kind(full) != "dir":
        io.err.write("The system cannot find the path specified.\n")
        run.errorlevel = 1
        return False
    run.dirs.append(run.cwd)
    run.cwd = run.world.spell(full)
    run.errorlevel = 0
    return True


def _popd(run: Run, words: list, text: str, io: Io) -> bool:
    if run.dirs:
        run.cwd = run.dirs.pop()
    return True


def _md(run: Run, words: list, text: str, io: Io) -> bool:
    ok = True
    for word in batch_args(text):
        full = run.path(word)
        if run.world.kind(full) is not None:
            io.err.write(f"A subdirectory or file {unquote(word)} already exists.\n")
            ok = False
            continue
        top = full
        while run.world.kind(top) is None and top != parent(top):
            top = parent(top)
        if run.world.kind(top) != "dir":
            io.err.write("The system cannot find the path specified.\n")
            ok = False
            continue
        run.ensure(full)
    run.errorlevel = 0 if ok else 1
    return ok


def _rd(run: Run, words: list, text: str, io: Io) -> bool:
    args = batch_args(text)
    tree = any(a.lower() == "/s" for a in args)
    ok = True
    for word in (a for a in args if not a.startswith("/")):
        full = run.path(word)
        if run.world.kind(full) != "dir":
            io.err.write("The system cannot find the file specified.\n")
            ok = False
            continue
        if run.world.listdir(full) and not tree:
            io.err.write("The directory is not empty.\n")
            ok = False
            continue
        if key(run.cwd).startswith(key(full) + "\\") or key(run.cwd) == key(full):
            io.err.write("The process cannot access the file because it is being used by "
                         "another process.\n")
            ok = False
            continue
        _remove_tree(run, full)
    run.errorlevel = 0 if ok else 1
    return ok


def _remove_tree(run: Run, folder: str) -> None:
    for name, is_dir in run.world.listdir(folder):
        path = child(folder, name)
        if is_dir:
            _remove_tree(run, path)
        else:
            run.world.remove(path)
    run.world.rmdir(folder)


def _del(run: Run, words: list, text: str, io: Io) -> bool:
    args = batch_args(text)
    deep = any(a.lower() == "/s" for a in args)
    for word in (a for a in args if not a.startswith("/")):
        full = run.path(word)
        if run.world.kind(full) == "dir":
            folder, pattern = full, "*"
        else:
            folder, pattern = parent(full), leaf(full)
        found = []
        for each in (list(run.tree(folder)) if deep and run.world.kind(folder) == "dir"
                     else [folder]):
            if run.world.kind(each) == "dir":
                found += [child(each, n) for n, is_dir in run.listing(each)
                          if not is_dir and matches(n, pattern)]
        if not found:
            io.err.write(f"Could Not Find {full}\n")
        for target in found:
            run.world.remove(target)
    # del leaves errorlevel be either way, and every script that checks has learnt it
    return True


def _copy(run: Run, words: list, text: str, io: Io) -> bool:
    args = [a for a in batch_args(text) if not re.fullmatch(r"/-?[a-z]", a, re.I)]
    if not args:
        io.err.write("The syntax of the command is incorrect.\n")
        run.errorlevel = 1
        return False
    if any("+" in unquote(a) for a in args):
        return _concat(run, args, io)
    source, target = args[0], args[1] if len(args) > 1 else "."
    full = run.path(source)
    if run.world.kind(full) == "dir":
        files = [child(full, n) for n, is_dir in run.listing(full) if not is_dir]
    else:
        files = [p for p in run.glob(source) if run.world.kind(p) == "file"]
    if not files:
        io.err.write("The system cannot find the file specified.\n        0 file(s) copied.\n")
        run.note(Step("copy", full, False, line=run.line))
        run.errorlevel = 1
        return False
    out = run.path(target)
    pattern = leaf(out) if wild(leaf(out)) else None
    if pattern is not None:
        out = parent(out)
    into = pattern is None and (run.world.kind(out) == "dir" or
                                unquote(target).endswith(("\\", "/")))
    if (into or pattern is not None) and run.world.kind(out) != "dir":
        io.err.write("The system cannot find the path specified.\n        0 file(s) copied.\n")
        # somewhere to copy to that isn't there, the game where it isn't say
        run.note(Step("copy", out, False, line=run.line))
        run.errorlevel = 1
        return False
    copied = 0
    if not into and pattern is None and len(files) > 1:
        # copy *.txt all.txt runs them together into one
        run.world.join(files, out)
        copied = 1
    else:
        for path in files:
            destination = child(out, mask(leaf(path), pattern)) if pattern is not None else \
                child(out, leaf(path)) if into else out
            if key(destination) == key(path):
                io.err.write("The file cannot be copied onto itself.\n")
                continue
            if run.world.kind(parent(destination)) != "dir":
                io.err.write("The system cannot find the path specified.\n")
                continue
            run.world.copy(path, destination)
            copied += 1
    io.out.write(f"        {copied} file(s) copied.\n")
    run.errorlevel = 0 if copied else 1
    if copied:
        run.note(Step("copy", out, True, line=run.line))
    return bool(copied)


def _concat(run: Run, args: list, io: Io) -> bool:
    """copy a+b c, or copy a+b into a. A + inside quotes is part of a name"""
    words = []
    for arg in args:
        words += [w for w in re.split(r'(\+)(?=(?:[^"]*"[^"]*")*[^"]*$)', arg) if w]
    target = words.pop() if len(words) > 1 and "+" not in words[-2:] else ""
    files = [path for word in words if word != "+" for path in run.glob(word)
             if run.world.kind(path) == "file"]
    if not files:
        io.err.write("The system cannot find the file specified.\n")
        run.errorlevel = 1
        return False
    out = run.path(target) if target else child(run.cwd, leaf(files[0]))
    if run.world.kind(out) == "dir":
        out = child(out, leaf(files[0]))
    # copy /b part1+part2 whole.bin: the parts of something too big to
    # host in one, so never held whole
    run.world.join(files, out)
    io.out.write("        1 file(s) copied.\n")
    run.errorlevel = 0
    return True


def _xcopy(run: Run, words: list, text: str, io: Io) -> bool:
    args = batch_args(text)
    flags = {a.lower() for a in args if a.startswith("/")}
    paths = [a for a in args if not a.startswith("/")]
    if not paths:
        io.err.write("Invalid number of parameters\n")
        run.errorlevel = 4
        return False
    source = run.path(paths[0])
    target = run.path(paths[1]) if len(paths) > 1 else run.cwd
    deep, empty = bool(flags & {"/s", "/e"}), "/e" in flags
    if run.world.kind(source) == "dir":
        base, pattern = source, "*"
    else:
        base, pattern = parent(source), leaf(source)
    if run.world.kind(base) != "dir" or (not wild(pattern) and run.world.kind(source) is None):
        io.err.write(f"File not found - {leaf(source)}\n0 File(s) copied\n")
        run.note(Step("copy", source, False, line=run.line))
        run.errorlevel = 4
        return False
    many = wild(pattern) or run.world.kind(source) == "dir"
    to_dir = run.world.kind(target) == "dir" or \
        (len(paths) > 1 and unquote(paths[1]).endswith(("\\", "/"))) or ("/i" in flags and many)
    if not to_dir and run.world.kind(target) is None:
        # xcopy asks whether a new target is a file or a folder. Piped an
        # answer, that's it; otherwise a folder for a folder or a wildcard, a
        # file for one file named like one
        said = (io.stdin or "").strip()[:1].upper()
        to_dir = said == "D" if said in ("D", "F") else (many or "." not in leaf(target))
    copied = 0
    for folder in (list(run.tree(base)) if deep else [base]):
        rel = folder[len(base):].lstrip("\\")
        landing = child(target, rel) if rel and to_dir else target
        if empty and to_dir:
            run.ensure(landing)
        for name, is_dir in run.listing(folder):
            if is_dir or not matches(name, pattern):
                continue
            src = child(folder, name)
            if to_dir:
                run.ensure(landing)
                destination = child(landing, name)
            else:
                run.ensure(parent(target))
                destination = target
            run.world.copy(src, destination)
            io.out.write(f"{src} -> {destination}\n")
            copied += 1
    io.out.write(f"{copied} File(s) copied\n")
    run.errorlevel = 0 if copied else 1
    return bool(copied)


def _robocopy(run: Run, words: list, text: str, io: Io) -> bool:
    plain, flags, lists, current = [], set(), {"/xf": [], "/xd": []}, None
    for a in batch_args(text):
        low = unquote(a).lower()
        if low.startswith("/"):
            name = low.split(":", 1)[0]
            current = name if name in lists else None
            flags.add(name)
        elif current is not None:
            lists[current].append(unquote(a))
        else:
            plain.append(unquote(a))
    if len(plain) < 2:
        run.errorlevel = 16
        return False
    source, target = run.path(plain[0]), run.path(plain[1])
    patterns = plain[2:] or ["*.*"]
    if run.world.kind(source) != "dir":
        io.err.write(f"ERROR 2 (0x00000002) Accessing Source Directory {source}\\\n")
        run.errorlevel = 16
        return False
    mirror = "/mir" in flags
    deep, empty = bool(flags & {"/s", "/e"}) or mirror, "/e" in flags or mirror
    moving = bool(flags & {"/mov", "/move"})
    # /XN /XO /XC between them leave every file that's already there alone
    lonely = bool(flags & {"/xn", "/xo", "/xc"})
    copied = extra = 0
    for folder in (list(run.tree(source)) if deep else [source]):
        rel = folder[len(source):].lstrip("\\")
        if rel and any(matches(part, x) for part in rel.split("\\") for x in lists["/xd"]):
            continue
        landing = child(target, rel) if rel else target
        files = [n for n, d in run.listing(folder) if not d and any(matches(n, p) for p in patterns)
                 and not any(matches(n, x) for x in lists["/xf"])]
        if files or empty or not rel:
            run.ensure(landing)
        for name in files:
            src, dst = child(folder, name), child(landing, name)
            if run.world.kind(dst) == "file" and (lonely or run.world.same(src, dst)):
                continue
            run.world.copy(src, dst)
            copied += 1
            if moving:
                run.world.remove(src)
    if mirror:
        for folder in list(run.tree(target)):
            rel = folder[len(target):].lstrip("\\")
            for name, is_dir in run.listing(folder):
                if not is_dir and run.world.kind(child(child(source, rel) if rel else source,
                                                      name)) != "file":
                    run.world.remove(child(folder, name))
                    extra += 1
    run.errorlevel = (1 if copied else 0) | (2 if extra else 0)
    return run.errorlevel < 8


def _move(run: Run, words: list, text: str, io: Io) -> bool:
    args = [a for a in batch_args(text) if not re.fullmatch(r"/-?y", a, re.I)]
    if len(args) < 2:
        io.err.write("The syntax of the command is incorrect.\n")
        run.errorlevel = 1
        return False
    sources = [s for s in run.glob(args[0], dirs=True) if run.world.kind(s) is not None]
    if not sources:
        io.err.write("The system cannot find the file specified.\n")
        run.note(Step("copy", run.path(args[0]), False, line=run.line))
        run.errorlevel = 1
        return False
    target = run.path(args[1])
    moved = 0
    for source in sources:
        destination = child(target, leaf(source)) if run.world.kind(target) == "dir" else target
        if key(destination) == key(source):
            continue
        if run.world.kind(parent(destination)) != "dir":
            io.err.write("The system cannot find the path specified.\n")
            continue
        if run.world.kind(destination) == "file":
            run.world.remove(destination)
        run.world.move(source, destination)
        moved += 1
    io.out.write(f"        {moved} file(s) moved.\n")
    run.errorlevel = 0 if moved else 1
    return bool(moved)


def _ren(run: Run, words: list, text: str, io: Io) -> bool:
    args = [a for a in batch_args(text) if not a.startswith("/")]
    if len(args) != 2 or re.search(r"[\\/]", unquote(args[1])):
        io.err.write("The syntax of the command is incorrect.\n")
        run.errorlevel = 1
        return False
    new = unquote(args[1])
    sources = [s for s in run.glob(args[0], dirs=True) if run.world.kind(s) is not None]
    if not sources:
        io.err.write("The system cannot find the file specified.\n")
        run.errorlevel = 1
        return False
    ok = True
    for source in sources:
        destination = child(parent(source), mask(leaf(source), new) if wild(new) else new)
        if run.world.kind(destination) is not None and key(destination) != key(source):
            io.err.write("A duplicate file name exists, or the file\ncannot be found.\n")
            ok = False
            continue
        run.world.move(source, destination)
    run.errorlevel = 0 if ok else 1
    return ok


def _type(run: Run, words: list, text: str, io: Io) -> bool:
    ok = True
    for word in batch_args(text):
        found = [p for p in run.glob(word) if run.world.kind(p) == "file"]
        if not found:
            io.err.write("The system cannot find the file specified.\n")
            ok = False
        for path in found:
            io.out.write(decode(run.world.read(path)))
    run.errorlevel = 0 if ok else 1
    return ok


def _quiet(run: Run, words: list, text: str, io: Io) -> bool:
    return True


def _ver(run: Run, words: list, text: str, io: Io) -> bool:
    # what a version check reads, Windows 10's unless the world says another:
    # nothing at all is no Windows it knows
    windows = getattr(run.world, "windows", "") or "10.0.19045.5011"
    io.out.write(f"\nMicrosoft Windows [Version {windows}]\n")
    return True


def _pause(run: Run, words: list, text: str, io: Io) -> bool:
    io.out.write("Press any key to continue . . . \n")
    return True


def _choice(run: Run, words: list, text: str, io: Io) -> bool:
    """CHOICE's letters and prompt, and the pick back in errorlevel"""
    letters, prompt, default = "YN", "", None
    args, n = split_args(text), 0
    while n < len(args):
        a, low = args[n], args[n].lower()
        taking = re.match(r"^/([cmdt])(:?)(.*)$", low)
        if taking:
            value = a[2 + len(taking.group(2)):]
            if not value and n + 1 < len(args):
                n += 1
                value = args[n]
            if taking.group(1) == "c":
                letters = value.upper() or letters
            elif taking.group(1) == "m":
                prompt = value
            elif taking.group(1) == "d":
                default = value.upper()[:1]
        elif not low.startswith("/"):
            prompt = f"{prompt} {a}".strip()
        n += 1
    if io.stdin is not None:
        picked = next((c for c in io.stdin.upper() if c in letters), None)
        run.errorlevel = letters.index(picked) + 1 if picked else 255
        return picked is not None
    answer = (run.ask("choice", prompt, options=letters) or default or letters[0]).upper()[:1]
    run.errorlevel = letters.index(answer) + 1 if answer in letters else 1
    io.out.write(f"{prompt} [{','.join(letters)}]?{answer}\n")
    # the question as answered is this one's, not the next one's
    run.asked_at = run.said
    return True


def _start(run: Run, words: list, text: str, io: Io) -> bool:
    """
    START, a program in a window of its own. The first word in quotes is
    that window's title, wherever it falls among the switches
    """
    spots = _spaced(text)
    n, titled, cwd = 0, False, run.cwd
    while n < len(spots):
        word = text[spots[n][0]:spots[n][1]]
        flag = word.lower()
        if word.startswith('"') and not titled:
            titled = True
        elif flag in ("/d", "/node", "/affinity") and n + 1 < len(spots):
            if flag == "/d":
                cwd = run.path(text[spots[n + 1][0]:spots[n + 1][1]])
            n += 1
        elif flag.startswith("/d") and len(flag) > 2:
            cwd = run.path(word[2:])
        elif not flag.startswith("/"):
            break
        n += 1
    if n >= len(spots):
        return True
    name = unquote(text[spots[n][0]:spots[n][1]])
    rest = text[spots[n][1]:].lstrip(" \t")
    # where the script is first, then the folder it's told to start in
    program = run.find_program(name)
    if program is None and cwd != run.cwd:
        here, run.cwd = run.cwd, cwd
        try:
            program = run.find_program(name)
        finally:
            run.cwd = here
    if program is None:
        bare = re.sub(r"\.(exe|com)$", "", name.lower())
        here, run.cwd = run.cwd, cwd
        try:
            code = SYSTEM[bare](run, rest, io) if bare in SYSTEM else \
                run.world.tool(bare, rest, cwd, io)
        finally:
            run.cwd = here
        # nothing that runs at all is a readme, a web page or a folder,
        # opened for the person: nothing to play, only noted
        if code is not None:
            run.errorlevel = code
        else:
            run.note(Step("opened", name, run.exists(name) or bool(URL.match(name)),
                          line=run.line))
        return True
    if program.lower().endswith((".bat", ".cmd")):
        # a cmd of its own: what it sets and where it goes stay there, and
        # its exit ends only it
        saved = dict(run.env), run.cwd, run.delayed
        run.cwd = cwd
        try:
            run.script_call(program, rest)
        except _Exit as e:
            run.errorlevel = e.code
        finally:
            run.env, run.cwd, run.delayed = saved
        return True
    run.errorlevel = run.world.program(program, rest, cwd, io)
    return True


def _spaced(text: str) -> list:
    """(start, end) of each word split at spaces and tabs outside quotes, the way START reads"""
    out, start, quoted = [], None, False
    for n, c in enumerate(text):
        if c == '"':
            quoted = not quoted
        if c in " \t" and not quoted:
            if start is not None:
                out.append((start, n))
                start = None
        elif start is None:
            start = n
    if start is not None:
        out.append((start, len(text)))
    return out


def _dir(run: Run, words: list, text: str, io: Io) -> bool:
    """DIR, bare or not, which scripts mostly read back through FOR /F"""
    args = batch_args(text)
    flags = [a.lower() for a in args if a.startswith("/")]
    bare, deep = "/b" in flags, "/s" in flags
    only = next((f[2:].lstrip(":") for f in flags if f.startswith("/a")), "")
    targets = [a for a in args if not a.startswith("/")] or ["*"]
    found = 0
    for target in targets:
        full = run.path(target)
        folder, pattern = (full, "*") if run.world.kind(full) == "dir" else (parent(full), leaf(full))
        if run.world.kind(folder) != "dir":
            continue
        for each in (list(run.tree(folder)) if deep else [folder]):
            for name, is_dir in run.listing(each):
                if not matches(name, pattern):
                    continue
                if (only == "d" and not is_dir) or (only == "-d" and is_dir):
                    continue
                found += 1
                shown = child(each, name) if deep else name
                if bare:
                    io.out.write(shown + "\n")
                else:
                    size = "" if is_dir else f"{run.world.size(child(each, name)):>14,}"
                    io.out.write(f"01/01/2000  12:00 PM    {'<DIR>' if is_dir else '     '}"
                                 f"{size} {name}\n")
    if not found:
        io.err.write("File Not Found\n")
        run.errorlevel = 1
        return False
    run.errorlevel = 0
    return True


def _path(run: Run, words: list, text: str, io: Io) -> bool:
    if text.strip():
        run.put("PATH", text.strip().lstrip("=").strip())
    return True


# cmd's own whose failing a script acts on: cd /d "%game%" || goto nogame
WATCHED = {"cd", "chdir", "pushd", "copy", "move", "del", "erase", "md", "mkdir", "rd", "rmdir",
           "ren", "rename", "type", "dir"}
BUILTINS = {
    "echo": _echo, "set": _set, "setlocal": _setlocal, "endlocal": _endlocal,
    "goto": _goto, "call": _call, "exit": _exit, "shift": _shift,
    "cd": _cd, "chdir": _cd, "pushd": _pushd, "popd": _popd,
    "md": _md, "mkdir": _md, "rd": _rd, "rmdir": _rd, "del": _del, "erase": _del,
    "copy": _copy, "move": _move, "ren": _ren, "rename": _ren, "type": _type,
    "start": _start, "pause": _pause, "path": _path, "dir": _dir,
    "cls": _quiet, "title": _quiet, "color": _quiet, "ver": _ver, "vol": _quiet,
    "verify": _quiet, "prompt": _quiet, "break": _quiet,
}
# the ones cmd still knows with something glued straight on: echo. cd.. call:x
GLUED = {"echo", "cd", "chdir", "md", "mkdir", "rd", "rmdir", "call", "goto", "set", "type",
         "pushd", "popd", "cls", "pause", "exit", "title", "dir"}


def _find(run: Run, text: str, io: Io) -> int:
    """
    FIND "text" [file]: the lines holding it, on what's piped in or in the
    files, each file's under a ---------- NAME line. "" is in no line, which
    is why find /c /v "" is how a script counts every line
    """
    args = split_args(text)
    flags = {a.lower() for a in args if a.startswith("/")}
    rest = [a for a in args if not a.startswith("/")]
    if not rest:
        return 2
    needle, files = rest[0], rest[1:]
    texts = [(None, io.stdin or "")] if not files else []
    for name in files:
        if not wild(leaf(name)):
            texts.append((name, run.text_of(name)))
            continue
        # every file the wildcard matches, each named the way it was asked for
        typed = name[:len(name) - len(leaf(name))]
        texts += [(typed + leaf(p), run.text_of(p)) for p in run.glob(name)] or [(name, None)]
    found_any = missing = False
    for name, body in texts:
        if body is None:
            io.err.write(f"File not found - {name.upper()}\n")
            missing = True
            continue
        lines = body.replace("\r\n", "\n").split("\n")
        if lines[-1] == "":
            lines.pop()
        hits = [line for line in lines
                if (bool(needle) and (needle.lower() in line.lower() if "/i" in flags
                                      else needle in line)) != ("/v" in flags)]
        found_any = found_any or bool(hits)
        head = "" if name is None else f"\n---------- {name.upper()}"
        if "/c" in flags:
            io.out.write(f"{head}: {len(hits)}\n" if head else f"{len(hits)}\n")
            continue
        if head:
            io.out.write(head + "\n")
        for line in hits:
            io.out.write(line + "\n")
    return 0 if found_any else 2 if missing else 1


def _findstr(run: Run, text: str, io: Io) -> int:
    args = split_args(text)
    flags = {a.lower()[:2] for a in args if a.startswith("/")}
    needles = [a[3:] for a in args if a.lower().startswith("/c:")]
    plain = [a for a in args if not a.startswith("/")]
    if not needles and plain:
        needles, plain = plain[0].split(), plain[1:]
    texts = [io.stdin or ""] if not plain else [run.text_of(f) or "" for f in plain]
    regex = "/l" not in flags
    hits = []
    for body in texts:
        for line in body.replace("\r\n", "\n").split("\n"):
            hit = False
            for needle in needles:
                if regex:
                    try:
                        hit = re.search(needle, line, re.I if "/i" in flags else 0) is not None
                    except re.error:
                        hit = needle.lower() in line.lower()
                else:
                    hit = (needle.lower() in line.lower()) if "/i" in flags else needle in line
                if hit:
                    break
            if hit != ("/v" in flags) and line:
                hits.append(line)
    for line in hits:
        io.out.write(line + "\n")
    return 0 if hits else 1


def _passthrough(run: Run, text: str, io: Io) -> int:
    io.out.write(io.stdin or "")
    return 0


def _sort(run: Run, text: str, io: Io) -> int:
    lines = [line for line in (io.stdin or "").replace("\r\n", "\n").split("\n") if line]
    io.out.write("".join(line + "\n" for line in sorted(lines, key=str.lower)))
    return 0


def _choice_tool(run: Run, text: str, io: Io) -> int:
    _choice(run, [], text, io)
    return run.errorlevel


def _xcopy_tool(run: Run, text: str, io: Io) -> int:
    _xcopy(run, [], text, io)
    return run.errorlevel


def _robocopy_tool(run: Run, text: str, io: Io) -> int:
    _robocopy(run, [], text, io)
    return run.errorlevel


def _where(run: Run, text: str, io: Io) -> int:
    found = 0
    for name in (a for a in split_args(text) if not a.startswith("/")):
        path = run.find_program(name)
        if path is not None:
            io.out.write(path + "\n")
            found += 1
    return 0 if found else 1


def _fc(run: Run, text: str, io: Io) -> int:
    paths = [a for a in split_args(text) if not a.startswith("/")]
    if len(paths) != 2:
        return 2
    a, b = (run.path(p) for p in paths)
    if run.world.kind(a) != "file" or run.world.kind(b) != "file":
        io.err.write("FC: cannot open the files\n")
        return 2
    same = run.world.same(a, b)
    io.out.write("FC: no differences encountered\n" if same else "Files are different\n")
    return 0 if same else 1


def _ok(run: Run, text: str, io: Io) -> int:
    return 0


def _cmd(run: Run, text: str, io: Io) -> int:
    """cmd /c, a child cmd: its variables and folder don't come back"""
    hit = re.match(r"\s*(?:/[a-bd-jl-z]\S*\s+)*/[ck]\s*(.*)$", text, re.I | re.S)
    if not hit:
        return 0
    command = hit.group(1).strip()
    if len(command) > 1 and command[0] == command[-1] == '"':
        command = command[1:-1]
    saved = (dict(run.env), run.cwd, run.delayed)
    try:
        seq, _ = parse(run.percent(command))
        run.seq(seq, {}, io)
    except _Exit as e:
        run.errorlevel = e.code
    except _Goto:
        pass
    finally:
        run.env, run.cwd, run.delayed = saved
    return run.errorlevel


def _no_match(run: Run, text: str, io: Io) -> int:
    return 1


def _whoami(run: Run, text: str, io: Io) -> int:
    # the high mandatory level is how a script tells it's running as admin,
    # which a play is unless the world's been told otherwise
    if "/groups" in text.lower():
        io.out.write("Mandatory Label\\High Mandatory Level Label S-1-16-12288\n"
                     if getattr(run.world, "admin", True) else
                     "Mandatory Label\\Medium Mandatory Level Label S-1-16-8192\n")
    else:
        io.out.write("pc\\player\n")
    return 0


def _admin_only(run: Run, text: str, io: Io) -> int:
    # fltmc, openfiles, fsutil, cacls on the system hive: the admin checks
    # scripts copy about, which only an admin gets past
    if getattr(run.world, "admin", True):
        return 0
    io.err.write("Access is denied.\n")
    return 5


def _tasklist(run: Run, text: str, io: Io) -> int:
    # the game isn't running: the drawer won't install while it is
    io.out.write("INFO: No tasks are running which match the specified criteria.\n")
    return 0


SYSTEM = {
    "find": _find, "findstr": _findstr, "more": _passthrough, "sort": _sort,
    "choice": _choice_tool, "xcopy": _xcopy_tool, "robocopy": _robocopy_tool,
    "where": _where, "fc": _fc, "comp": _fc, "timeout": _ok, "ping": _ok,
    "attrib": _ok, "icacls": _ok, "takeown": _ok, "mode": _ok, "chcp": _ok,
    "tasklist": _tasklist, "taskkill": _no_match, "whoami": _whoami, "hostname": _ok,
    "cmdkey": _ok, "openfiles": _admin_only, "cmd": _cmd, "fsutil": _admin_only,
    "fltmc": _admin_only, "cacls": _admin_only,
}

