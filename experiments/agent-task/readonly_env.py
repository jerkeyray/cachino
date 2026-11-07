"""Answer read-only shell commands from a frozen file snapshot, in Python.

Nothing is executed: commands are parsed with shlex and served from an
in-memory {path: text} snapshot of the task repository at its baseline
revision, rooted at /task. Supported: ls, find -name, cat, head, tail, nl -ba,
sed -n 'A,Bp', grep (-n, -r, -i, -l), wc -l and pwd, joined by pipes, with an
optional leading `cd /task &&`. Anything else, including every edit, returns a
fixed "not available" message with return code 127, so outputs depend on the
command without the environment ever changing.

This keeps agent observations realistic in content and length. It does not
reproduce a real shell: unsupported syntax gets the fixed message, and edits
never take effect, so a session can explore but not change the code.
"""
import fnmatch
import posixpath
import re
import shlex

ROOT = "/task"
UNAVAILABLE = "This environment answers read-only commands only (ls, find, cat, head, tail, nl, sed -n, grep, wc). The command was not run."
SUBMIT = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"


class Unsupported(Exception):
    """The command is outside the emulated read-only subset."""


class ReadOnlyFiles:
    """A frozen snapshot with path resolution confined to /task."""

    def __init__(self, files):
        self.files = dict(files)
        self.dirs = {""}
        for path in self.files:
            parts = path.split("/")
            for i in range(1, len(parts)):
                self.dirs.add("/".join(parts[:i]))

    def resolve(self, arg):
        """Return a snapshot-relative path, or raise if it leaves /task."""
        path = posixpath.normpath(posixpath.join(ROOT, arg))
        if path == ROOT:
            return ""
        if not path.startswith(ROOT + "/"):
            raise FileNotFoundError(arg)
        return path[len(ROOT) + 1:]

    def text(self, arg):
        rel = self.resolve(arg)
        if rel in self.files:
            return self.files[rel]
        if rel in self.dirs:
            raise IsADirectoryError(arg)
        raise FileNotFoundError(arg)

    def listing(self, rel):
        prefix = rel + "/" if rel else ""
        names = set()
        for path in list(self.files) + list(self.dirs):
            if path.startswith(prefix) and path != rel:
                names.add(path[len(prefix):].split("/")[0])
        return sorted(names)

    def walk(self, rel):
        prefix = rel + "/" if rel else ""
        return sorted(p for p in self.files if p.startswith(prefix) or p == rel)


def _lines(text):
    return text.splitlines(keepends=True)


def _range(spec):
    match = re.fullmatch(r"'?(\d+),(\d+)p'?", spec) or re.fullmatch(r"'?(\d+)p'?", spec)
    if not match:
        raise Unsupported(spec)
    first = int(match.group(1))
    last = int(match.group(2)) if match.lastindex == 2 else first
    return first, last


def _count(args, default=10):
    if not args:
        return default, []
    if args[0] == "-n" and len(args) > 1:
        return int(args[1]), args[2:]
    if re.fullmatch(r"-n?\d+", args[0]):
        return int(args[0].lstrip("-n")), args[1:]
    return default, args


def _grep(fs, args, stdin):
    flags = set()
    while args and args[0].startswith("-") and len(args[0]) > 1:
        for flag in args[0][1:]:
            if flag not in "nril":
                raise Unsupported("grep -" + flag)
            flags.add(flag)
        args = args[1:]
    if not args:
        raise Unsupported("grep without pattern")
    pattern, paths = args[0], args[1:]
    regex = re.compile(pattern, re.IGNORECASE if "i" in flags else 0)
    sources = []
    if not paths:
        if stdin is None:
            raise Unsupported("grep without input")
        sources = [(None, stdin)]
    for arg in paths:
        rel = fs.resolve(arg)
        if rel in fs.dirs and rel not in fs.files:
            if "r" not in flags:
                raise IsADirectoryError(arg)
            sources += [(posixpath.join(arg, p[len(rel) + 1:] if rel else p), fs.files[p]) for p in fs.walk(rel)]
        else:
            sources.append((arg, fs.text(arg)))
    out = []
    show_name = len(sources) > 1 or "r" in flags
    for name, text in sources:
        hit = False
        for number, line in enumerate(_lines(text), 1):
            if regex.search(line):
                hit = True
                if "l" in flags:
                    break
                prefix = (name + ":" if show_name and name else "") + (str(number) + ":" if "n" in flags else "")
                out.append(prefix + line if line.endswith("\n") else prefix + line + "\n")
        if hit and "l" in flags:
            out.append(name + "\n")
    return "".join(out), 0 if out else 1


def _run_one(fs, argv, stdin):
    """Run one pipeline stage; return (text, returncode)."""
    name, args = argv[0], argv[1:]
    if name == "pwd" and not args:
        return ROOT + "\n", 0
    if name == "ls":
        opts = [a for a in args if a.startswith("-")]
        if any(set(o[1:]) - set("la1") for o in opts):
            raise Unsupported("ls option")
        targets = [a for a in args if not a.startswith("-")] or ["."]
        out = []
        for target in targets:
            rel = fs.resolve(target)
            if rel in fs.files:
                out.append(target + "\n")
            elif rel in fs.dirs:
                out += [n + "\n" for n in fs.listing(rel)]
            else:
                return "ls: cannot access '%s': No such file or directory\n" % target, 2
        return "".join(out), 0
    if name == "find":
        if len(args) != 3 or args[1] != "-name":
            raise Unsupported("find form")
        rel = fs.resolve(args[0])
        base = args[0].rstrip("/")
        hits = [base + "/" + p[len(rel) + 1:] if rel else base + "/" + p for p in fs.walk(rel)
                if fnmatch.fnmatch(posixpath.basename(p), args[2])]
        return "".join(h + "\n" for h in hits), 0
    if name in ("cat", "head", "tail", "nl", "wc", "sed"):
        if name == "sed":
            if len(args) < 2 or args[0] != "-n":
                raise Unsupported("sed form")
            first, last = _range(args[1])
            text = fs.text(args[2]) if len(args) > 2 else stdin
            if text is None:
                raise Unsupported("sed without input")
            return "".join(_lines(text)[first - 1:last]), 0
        if name in ("head", "tail"):
            count, rest = _count(args)
            text = fs.text(rest[0]) if rest else stdin
            if text is None:
                raise Unsupported(name + " without input")
            lines = _lines(text)
            return "".join(lines[:count] if name == "head" else lines[-count:] if count else []), 0
        if name == "nl":
            rest = [a for a in args if a != "-ba"]
            text = fs.text(rest[0]) if rest else stdin
            if text is None:
                raise Unsupported("nl without input")
            return "".join("%6d\t%s" % (i, line) for i, line in enumerate(_lines(text), 1)), 0
        if name == "wc":
            if not args or args[0] != "-l":
                raise Unsupported("wc form")
            if len(args) > 1:
                return "%d %s\n" % (len(_lines(fs.text(args[1]))), args[1]), 0
            return "%d\n" % len(_lines(stdin or "")), 0
        if len(args) != 1:
            raise Unsupported("cat form")
        return fs.text(args[0]), 0
    if name == "grep":
        return _grep(fs, args, stdin)
    raise Unsupported(name)


def respond(fs, command):
    """Return {"output", "returncode", "exception_info"} for one action string."""
    command = command.strip()
    if command.split("\n")[0].strip() in ("echo " + SUBMIT, "echo '%s'" % SUBMIT, 'echo "%s"' % SUBMIT):
        return {"output": SUBMIT + "\n", "returncode": 0, "exception_info": ""}
    command = re.sub(r"^cd\s+/task\s*&&\s*", "", command)
    try:
        if any(token in command for token in (";", "&&", "||", ">", "<", "`", "$(", "\n")):
            raise Unsupported("shell syntax")
        text, code = None, 0
        for stage in command.split("|"):
            argv = shlex.split(stage)
            if not argv:
                raise Unsupported("empty stage")
            text, code = _run_one(fs, argv, text)
        return {"output": text, "returncode": code, "exception_info": ""}
    except Unsupported:
        return {"output": UNAVAILABLE + "\n", "returncode": 127, "exception_info": ""}
    except IsADirectoryError as error:
        return {"output": "%s: Is a directory\n" % error.args[0], "returncode": 1, "exception_info": ""}
    except (FileNotFoundError, ValueError) as error:
        target = error.args[0] if error.args else command
        return {"output": "%s: No such file or directory\n" % target, "returncode": 1, "exception_info": ""}
