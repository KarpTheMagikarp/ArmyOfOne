"""
A model conversion .txt, read the way FF XIII HD Models.exe reads one.

The exe never parses a recipe up front. It fgets() a line at a time out of
one open file, and whichever part of it is running (the header, the block
list, the editor for the block it's on) takes the lines it wants and leaves
the rest for whoever's next. So this is a cursor, not a parse tree, and every
editor shares it.

Lines starting `::` are the reader's own and never reach a caller:

    ::A,bit,need,Question (Y/N)?   asked unless `need` is all set already,
                                   then `bit` set or cleared by the answer
    ::I,mask,want                  what follows skipped unless answers & mask == want
    ::E                            ends a skip
    ::B,n,trb,block                block n of the output comes from there instead
    ::F,flags                      the recipe's flags word, replaced
    ::P,text[,nl]                  printed
"""

from patcher.mods.hdmodels.cnum import atof, atoi, f32


class Recipe:
    def __init__(self, raw: bytes, ask=None, say=None):
        # text mode: CRLF reads as LF, and ^Z ends the file
        text = raw.split(b"\x1a", 1)[0].replace(b"\r\n", b"\n")
        self.text = text.decode("latin-1")
        self.at = 0
        self.eof = False
        self.ask = ask or (lambda question: False)
        self.say = say or (lambda text: None)
        # the exe's globals the reader writes to
        self.answers = 0
        self.flags = 0
        self.out_trb = []
        self.out_block = []
        # what the argument reads walk, and the raw line errors quote
        self.line = ""
        self.rest = ""
        self.copy = ""
        self._skip = False

    def _fgets(self, size: int) -> str:
        if self.at >= len(self.text):
            self.eof = True
            return ""
        end = self.text.find("\n", self.at, self.at + size - 1)
        stop = end + 1 if end >= 0 else min(self.at + size - 1, len(self.text))
        got = self.text[self.at:stop]
        self.at = stop
        if self.at >= len(self.text) and not got.endswith("\n"):
            # ran into the end of the file rather than a newline
            self.eof = True
        return got

    def read_line(self, size: int = 0xFF):
        """FUN_0040a3a0: the next line a caller gets, None at the end of the file"""
        self._skip = False
        while True:
            line = self._fgets(size)
            if line.endswith("\n") and 0 < len(line) <= size:
                line = line[:-1]
            self.copy = line[:0xFF]
            if line.startswith("::"):
                line = ":/" + line[2:]
                self._skip = self._skip and line[2:3] != "E"
                if not self._skip:
                    self._directive(line)
            self.line = line
            if self.eof or (not line.startswith(":/") and not self._skip):
                return None if self.eof and line == "" else line

    def _directive(self, line: str) -> None:
        self.rest = line.partition(",")[2]
        kind = line[2:3]
        if kind == "A":
            bit, need = self.int(), self.int()
            if self.answers & need != need:
                question = self.str()
                self.say(question + " ")
                yes = bool(self.ask(question))
                self.say("Yes\n" if yes else "No\n")
                self.answers = (self.answers | bit) if yes else (self.answers & ~bit)
        elif kind == "B":
            n, trb, block = self.int(), self.int(), self.int()
            self.out_trb[n] = trb & 0xFFFF
            self.out_block[n] = block & 0xFFFF
        elif kind == "F":
            self.flags = self.int()
        elif kind == "I":
            mask, want = self.int(), self.int()
            self._skip = (mask & self.answers) != want
        elif kind == "P":
            text = self.str()
            newline = self.int(optional=True)
            self.say(text + ("\n" if newline else ""))

    def command(self, size: int = 0xFF):
        """the next line's first field, its other fields left for int()/float()/str()"""
        line = self.read_line(size)
        if line is None:
            return None
        head, _, self.rest = line.partition(",")
        return head

    def _token(self, optional: bool) -> str:
        if self.rest == "":
            if not optional:
                self.say(f"Error: Insufficient number of arguments encountered. ({self.copy})\n")
            return ""
        token, _, self.rest = self.rest.partition(",")
        return token

    def int(self, optional: bool = False) -> int:
        return atoi(self._token(optional))

    def float(self, optional: bool = False) -> float:
        return f32(atof(self._token(optional)))

    def double(self, optional: bool = False) -> float:
        return atof(self._token(optional))

    def str(self, optional: bool = False, default: str = "") -> str:
        if self.rest == "":
            if optional:
                return default
            self.say(f"Error: Insufficient number of arguments encountered. ({self.copy})\n")
            return ""
        token, _, self.rest = self.rest.rstrip("\n").partition(",")
        return token
