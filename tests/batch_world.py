"""
A PC in a dict, for playing .bat files against in tests: files and folders
keyed the way Windows compares them, programs as Python callables, and
questions answered off a list.
"""

from patcher.mods import batch


class Mem:
    def __init__(self, files=None, folders=(), answers=(), programs=None, tools=None):
        self.files, self.folders = {}, {}
        self.answers, self.asked = list(answers), []
        self.programs, self.tools = dict(programs or {}), dict(tools or {})
        self.ran = []
        for drive in ("C:\\",):
            self.folders[batch.key(drive)] = drive
        for folder in folders:
            self.mkdirs(folder)
        for path, data in (files or {}).items():
            self.put(path, data)

    def mkdirs(self, path):
        path = batch.norm(path)
        chain = []
        while batch.key(path) not in self.folders:
            chain.append(path)
            if batch.parent(path) == path:
                break
            path = batch.parent(path)
        for each in reversed(chain):
            self.folders[batch.key(each)] = each

    def put(self, path, data):
        path = batch.norm(path)
        self.mkdirs(batch.parent(path))
        self.files[batch.key(path)] = (path, data if isinstance(data, bytes) else data.encode())

    def text(self, path):
        return self.files[batch.key(path)][1].decode("utf-8", "surrogateescape")

    def kind(self, path):
        k = batch.key(path)
        return "file" if k in self.files else "dir" if k in self.folders else None

    def listdir(self, folder):
        k = batch.key(folder).rstrip("\\") + "\\"
        out = []
        for table, is_dir in ((self.files, False), (self.folders, True)):
            for key, value in table.items():
                spelled = value[0] if not is_dir else value
                if key.startswith(k) and key[len(k):] and "\\" not in key[len(k):]:
                    out.append((batch.leaf(spelled), is_dir))
        return out

    def read(self, path):
        return self.files[batch.key(path)][1]

    def write(self, path, data, append=False):
        path = batch.norm(path)
        k = batch.key(path)
        before = self.files[k][1] if append and k in self.files else b""
        spelled = self.files[k][0] if k in self.files else path
        self.files[k] = (spelled, before + data)

    def copy(self, source, target):
        self.write(target, self.read(source))

    def join(self, sources, target):
        self.write(target, b"".join(self.read(s) for s in sources))

    def move(self, source, target):
        source, target = batch.norm(source), batch.norm(target)
        sk, tk = batch.key(source), batch.key(target)
        if sk in self.files:
            data = self.files.pop(sk)[1]
            self.files[tk] = (target, data)
            return
        prefix = sk + "\\"
        for table in (self.files, self.folders):
            for k in [k for k in table if k == sk or k.startswith(prefix)]:
                value = table.pop(k)
                rest = (value[0] if table is self.files else value)[len(source):]
                moved = target + rest
                table[batch.key(moved)] = (moved, value[1]) if table is self.files else moved

    def remove(self, path):
        self.files.pop(batch.key(path), None)

    def mkdir(self, path):
        self.folders[batch.key(path)] = batch.norm(path)

    def rmdir(self, path):
        self.folders.pop(batch.key(path), None)

    def size(self, path):
        return len(self.read(path))

    def same(self, a, b):
        return self.read(a) == self.read(b)

    def spell(self, path):
        k = batch.key(path)
        if k in self.files:
            return self.files[k][0]
        return self.folders.get(k, batch.norm(path))

    def script(self, path):
        return batch.Script.read(self.spell(path), self.read(path))

    def program(self, path, text, cwd, io):
        self.ran.append((batch.leaf(path), text, cwd))
        handler = self.programs.get(batch.leaf(path).lower())
        if handler is None:
            return 0
        return handler(self, batch.split_args(text), cwd, io)

    def tool(self, name, text, cwd, io):
        handler = self.tools.get(name)
        return None if handler is None else handler(self, text, cwd, io)

    def answer(self, ask):
        self.asked.append(ask)
        return self.answers.pop(0) if self.answers else ""


def play(text, files=None, folders=(), answers=(), programs=None, tools=None, args=(),
         at="C:\\mod", env=None, name="install.bat"):
    """(the console, the world, the run, the exit code) for a script played in `at`"""
    world = Mem(files, folders, answers, programs, tools)
    world.mkdirs(at)
    path = batch.child(at, name)
    world.put(path, text.replace("\n", "\r\n"))
    run = batch.Run(world, dict(env or {"PATHEXT": ".COM;.EXE;.BAT;.CMD"}), at)
    code = run.run(world.script(path), [f'"{path}"', *args])
    return run.console, world, run, code
