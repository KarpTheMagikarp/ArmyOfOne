"""
A FOMOD installer's choices, read rather than run.

A Nexus mod that comes in options often says which with fomod/ModuleConfig.xml:
files that always go in, then steps of groups of plugins, each plugin folders
and files mapped to where they go, and flags its picks set that turn more files
on further down. Vortex and MO2 put the questions up in a wizard. Here each one
is the drawer's own version question, a group at a time, and what the answers
come to is a plain list of the mod's files under the paths they'd have been
installed to, which the drawer then reads like any other drop.

A group where one or none is picked is one question. One where any number can
be is a yes or no per plugin, since the drawer's question takes one answer.
Required plugins go in and ones marked not usable stay out without asking, and
a group of one required plugin, or a SelectAll, asks nothing.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

CONFIG = "fomod/moduleconfig.xml"
INFO = "fomod/info.xml"
YES, NO = "Install it", "Leave it out"
NONE = "None of them"


class FomodError(ValueError):
    pass


@dataclass
class Plugin:
    name: str
    about: str = ""
    files: list = field(default_factory=list)   # (source, destination, folder, priority)
    flags: dict = field(default_factory=dict)
    kind: str = "Optional"
    # (condition, kind) patterns that change kind with what's been picked
    kinds: list = field(default_factory=list)


@dataclass
class Group:
    step: str
    name: str
    kind: str
    plugins: list = field(default_factory=list)
    visible: object = None


@dataclass
class Config:
    name: str
    root: str            # the folder fomod/ sits in, "" for the top of the drop
    required: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    conditional: list = field(default_factory=list)   # (condition, files)


@dataclass
class Question:
    key: str
    title: str
    options: list
    recommended: str | None = None
    about: dict = field(default_factory=dict)
    # whether another could follow, as far as the picks so far say
    more: bool = False


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _kids(node, name: str) -> list:
    return [n for n in node if _local(n.tag) == name]


def _kid(node, name: str):
    return next(iter(_kids(node, name)), None)


def _text(node) -> str:
    return " ".join((node.text or "").split()) if node is not None else ""


def _decode(raw: bytes) -> str:
    """FOMOD tools write UTF-16 with a BOM as often as UTF-8"""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8", errors="replace")
    if len(raw) > 1 and raw[1:2] == b"\x00":
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _parse_xml(raw: bytes):
    text = _decode(raw)
    # the declaration says an encoding the text has already been decoded out of
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        raise FomodError(f"its fomod/ModuleConfig.xml won't read ({e})") from e


def _ordered(nodes: list, node) -> list:
    order = (node.get("order") or "Ascending") if node is not None else "Explicit"
    if order == "Explicit":
        return nodes
    named = sorted(nodes, key=lambda n: (n.get("name") or "").lower())
    return named[::-1] if order == "Descending" else named


def _files(node) -> list:
    out = []
    for item in list(node) if node is not None else ():
        tag = _local(item.tag)
        if tag not in ("file", "folder"):
            continue
        source = (item.get("source") or "").replace("\\", "/").strip("/")
        if not source:
            continue
        destination = item.get("destination")
        if destination is None:
            # a file with no destination goes in under its own path, a
            # folder's contents at the top
            destination = source if tag == "file" else ""
        try:
            priority = int(item.get("priority") or 0)
        except ValueError:
            priority = 0
        out.append((source, destination.replace("\\", "/").strip("/"), tag == "folder", priority))
    return out


def _condition(node):
    """a dependencies node as ("and"|"or", [conditions]), or None for always"""
    if node is None:
        return None
    parts = []
    for item in node:
        tag = _local(item.tag)
        if tag == "flagdependency":
            parts.append(("flag", item.get("flag") or "", item.get("value") or ""))
        elif tag == "filedependency":
            parts.append(("file", item.get("file") or "", item.get("state") or ""))
        elif tag == "dependencies":
            parts.append(_condition(item))
    return ((node.get("operator") or "And").lower(), parts)


def holds(condition, flags: dict) -> bool:
    """
    whether a condition holds for the flags set so far. Files other mods
    install can't be known here, so one asked to be there isn't, and one asked
    to be missing is
    """
    if condition is None:
        return True
    operator, parts = condition
    results = []
    for part in parts:
        if part is None:
            results.append(True)
        elif part[0] == "flag":
            results.append(flags.get(part[1], "") == part[2])
        elif part[0] == "file":
            results.append(part[2].lower() == "missing")
        else:
            results.append(holds(part, flags))
    if not results:
        return True
    return any(results) if operator == "or" else all(results)


def find(members) -> str | None:
    """where ModuleConfig.xml is in a drop's members, at the top or a folder down"""
    for member, _ in members:
        key = member.replace("\\", "/").lower()
        if key == CONFIG or (key.endswith("/" + CONFIG) and key.count("/") == 2):
            return member
    return None


def read(source, members) -> Config | None:
    """a drop's FOMOD, or None when it hasn't one"""
    at = find(members)
    if at is None:
        return None
    root = at.replace("\\", "/")[:-len(CONFIG)].strip("/")
    xml = _parse_xml(source.read(at))
    name = _text(_kid(xml, "modulename"))
    info = next((m for m, _ in members
                 if m.replace("\\", "/").lower() == (f"{root}/{INFO}" if root else INFO)), None)
    if info is not None and not name:
        try:
            name = _text(_kid(_parse_xml(source.read(info)), "name"))
        except FomodError:
            pass
    config = Config(name=name, root=root, required=_files(_kid(xml, "requiredinstallfiles")))
    steps = _kid(xml, "installsteps")
    for step in _ordered(_kids(steps, "installstep"), steps) if steps is not None else ():
        visible = _condition(_kid(_kid(step, "visible"), "dependencies")) \
            if _kid(step, "visible") is not None else None
        if visible is None and _kid(step, "visible") is not None:
            # a bare visible holding flag dependencies with no dependencies round them
            visible = _condition(_kid(step, "visible"))
        groups = _kid(step, "optionalfilegroups")
        for group in _ordered(_kids(groups, "group"), groups) if groups is not None else ():
            plugins = _kid(group, "plugins")
            found = Group(step=step.get("name") or "", name=group.get("name") or "",
                          kind=group.get("type") or "SelectAny", visible=visible)
            for plugin in _ordered(_kids(plugins, "plugin"), plugins) if plugins is not None else ():
                item = Plugin(name=plugin.get("name") or "", about=_text(_kid(plugin, "description")),
                              files=_files(_kid(plugin, "files")))
                for flag in _kids(_kid(plugin, "conditionflags"), "flag") \
                        if _kid(plugin, "conditionflags") is not None else ():
                    item.flags[flag.get("name") or ""] = flag.text or ""
                described = _kid(plugin, "typedescriptor")
                plain = _kid(described, "type") if described is not None else None
                if plain is not None:
                    item.kind = plain.get("name") or "Optional"
                depends = _kid(described, "dependencytype") if described is not None else None
                if depends is not None:
                    default = _kid(depends, "defaulttype")
                    item.kind = default.get("name") if default is not None else "Optional"
                    for pattern in _kids(_kid(depends, "patterns"), "pattern") \
                            if _kid(depends, "patterns") is not None else ():
                        kind = _kid(pattern, "type")
                        item.kinds.append((_condition(_kid(pattern, "dependencies")),
                                           kind.get("name") if kind is not None else "Optional"))
                found.plugins.append(item)
            config.groups.append(found)
    patterns = _kid(_kid(xml, "conditionalfileinstalls"), "patterns") \
        if _kid(xml, "conditionalfileinstalls") is not None else None
    for pattern in _kids(patterns, "pattern") if patterns is not None else ():
        config.conditional.append((_condition(_kid(pattern, "dependencies")),
                                   _files(_kid(pattern, "files"))))
    return config


def _kind(plugin: Plugin, flags: dict) -> str:
    for condition, kind in plugin.kinds:
        if holds(condition, flags):
            return kind
    return plugin.kind


def _flags(config: Config, picked: list) -> dict:
    flags = {}
    for plugin in picked:
        flags.update(plugin.flags)
    return flags


def walk(config: Config, answers: dict) -> tuple:
    """
    (the plugins picked so far, the next question or None once every group
    has its answer). `answers` is question key -> the option picked
    """
    picked, question = _walk(config, answers)
    if question is not None:
        # every later group answered its way, to see whether one still asks
        rest = dict(answers, **{question.key: question.recommended or question.options[0]})
        question.more = _walk(config, rest)[1] is not None
    return picked, question


def _walk(config: Config, answers: dict) -> tuple:
    picked = []
    for g, group in enumerate(config.groups):
        flags = _flags(config, picked)
        if not holds(group.visible, flags):
            continue
        usable = [p for p in group.plugins if _kind(p, flags) != "NotUsable"]
        must = [p for p in usable if _kind(p, flags) == "Required"]
        rest = [p for p in usable if p not in must]
        picked += must
        kind = group.kind
        if kind == "SelectAll" or not rest:
            picked += rest if kind == "SelectAll" else []
            continue
        label = group.name or group.step or config.name or "Options"
        if kind in ("SelectExactlyOne", "SelectAtMostOne"):
            if must:
                # a required one is the one
                continue
            key = f"{g}"
            options = [p.name for p in rest] + ([NONE] if kind == "SelectAtMostOne" else [])
            if len(rest) == 1 and kind == "SelectExactlyOne":
                picked += rest
                continue
            if key not in answers:
                recommended = next((p.name for p in rest if _kind(p, flags) == "Recommended"),
                                   None)
                return picked, Question(key, label, options, recommended,
                                        {p.name: p.about for p in rest if p.about})
            picked += [p for p in rest if p.name == answers[key]][:1]
            continue
        # any number of them: a yes or no each
        for n, plugin in enumerate(rest):
            key = f"{g}.{n}"
            if key not in answers:
                recommended = YES if _kind(plugin, flags) == "Recommended" else None
                return picked, Question(key, f"{label}: {plugin.name}", [YES, NO], recommended,
                                        {YES: plugin.about} if plugin.about else {})
            if answers[key] == YES:
                picked.append(plugin)
    return picked, None


def installed(config: Config, picked: list, members) -> dict:
    """
    destination path -> the member that goes there, for the required files,
    the picked plugins' and the conditional ones their flags turn on. A higher
    priority wins a path, and among equals the later one, as the installers do
    """
    flags = _flags(config, picked)
    mappings = list(config.required)
    for plugin in picked:
        mappings += plugin.files
    for condition, files in config.conditional:
        if holds(condition, flags):
            mappings += files
    by_key = {}
    for member, _ in members:
        by_key[member.replace("\\", "/").lower()] = member
    base = f"{config.root.lower()}/" if config.root else ""
    out, ranks = {}, {}
    for order, (source, destination, folder, priority) in enumerate(mappings):
        start = base + source.lower()
        if folder:
            for key, member in by_key.items():
                if key.startswith(start + "/"):
                    rel = member.replace("\\", "/")[len(start) + 1:]
                    _put(out, ranks, f"{destination}/{rel}".strip("/"), member, (priority, order))
        elif start in by_key:
            _put(out, ranks, destination or source.rsplit("/", 1)[-1], by_key[start],
                 (priority, order))
    return out


def _put(out: dict, ranks: dict, path: str, member: str, rank: tuple) -> None:
    key = path.lower()
    if key not in ranks or rank >= ranks[key]:
        out.pop(next((p for p in out if p.lower() == key), None), None)
        out[path] = member
        ranks[key] = rank


class Chosen:
    """
    a drop seen as what its FOMOD's answers install: each member under the
    path it goes to, and nothing the answers left out. What the drawer reads
    in place of the drop from then on
    """

    def __init__(self, source, mapping: dict):
        self._source, self._to = source, dict(mapping)
        self.path, self.root, self.context = source.path, None, list(source.context)
        # one member per path, so nothing's doubled; what didn't open is the
        # drop's whatever was picked
        self.doubled, self.nested = [], []
        self.unopened, self.blocked = list(source.unopened), list(source.blocked)
        self._sizes = dict(source.members())

    def members(self) -> list:
        return [(path, self._sizes.get(member, 0)) for path, member in self._to.items()]

    def has(self, member: str) -> bool:
        return member in self._to

    def read(self, member: str) -> bytes:
        return self._source.read(self._to[member])

    def opened(self, member: str):
        return self._source.opened(self._to[member])

    def head(self, member: str, size: int = 8) -> bytes:
        # the sandbox tells a zip from a .7z by this, so without it a zip a
        # picked .bat unpacks read as one only 7-Zip opens
        return self._source.head(self._to[member], size)

    def real(self, member: str) -> str:
        """what it's called in the download, which is where a lost copy is read back from"""
        return getattr(self._source, "real", lambda m: m)(self._to[member])

    def listing(self, outer: str) -> list:
        return []

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
