#!/usr/bin/env python3
"""
Validate the generated schematic with KiCad itself.

  1. every sheet must load standalone (catches malformed symbol caches)
  2. the whole hierarchy must load and produce a netlist
  3. KiCad's extracted connectivity must match netlist.txt exactly

Exits non-zero on any discrepancy.  Requires kicad-cli.

Usage: python3 gen/validate.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.join(PROJ, "esp32s3-can-sd-logger.kicad_sch")

TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')


def parse(text):
    toks = TOKEN.findall(text)
    pos = 0

    def read():
        nonlocal pos
        tok = toks[pos]
        pos += 1
        if tok == "(":
            out = []
            while toks[pos] != ")":
                out.append(read())
            pos += 1
            return out
        return tok

    out = []
    while pos < len(toks):
        out.append(read())
    return out


def unq(s):
    return s[1:-1] if s.startswith('"') else s


def kids(node, tag):
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def kicad_netlist(sch, workdir):
    out = os.path.join(workdir, "n.net")
    res = subprocess.run(
        ["kicad-cli", "sch", "export", "netlist", "--output", out, sch],
        capture_output=True, text=True)
    if res.returncode != 0:
        return None, (res.stdout + res.stderr).strip()
    return out, ""


def main():
    failures = []
    tmp = tempfile.mkdtemp()
    try:
        # 1. each sheet standalone
        for name in sorted(os.listdir(PROJ)):
            if not name.endswith(".kicad_sch"):
                continue
            solo_dir = os.path.join(tmp, "solo_" + name[:-10])
            os.makedirs(solo_dir, exist_ok=True)
            solo = os.path.join(solo_dir, "solo.kicad_sch")
            shutil.copy(os.path.join(PROJ, name), solo)
            _, err = kicad_netlist(solo, solo_dir)
            # The root sheet references its children by relative path, so it is
            # expected to fail in isolation; every block sheet must not.
            if err and name != os.path.basename(ROOT):
                failures.append("sheet %s failed to load: %s" % (name, err))
            else:
                print("  load  %-28s ok" % name)

        # 2 + 3. full hierarchy and connectivity
        net, err = kicad_netlist(ROOT, tmp)
        if err:
            failures.append("hierarchy failed to load: " + err)
            print("\n".join(failures))
            return 1

        root = parse(open(net, encoding="utf-8").read())[0]
        got = {}
        for n in kids(kids(root, "nets")[0], "net"):
            name = unq(kids(n, "name")[0][1]).split("/")[-1]
            got[name] = {
                (unq(kids(nd, "ref")[0][1]), unq(kids(nd, "pin")[0][1]))
                for nd in kids(n, "node")
            }
        n_comp = len(kids(kids(root, "components")[0], "comp"))

        want = {}
        for line in open(os.path.join(PROJ, "netlist.txt"),
                         encoding="utf-8").read().splitlines()[3:]:
            if not line.strip():
                continue
            f = line.split()
            want[f[0]] = {tuple(x.split(".", 1)) for x in f[1:]}

        for name, nodes in sorted(want.items()):
            # PWR_FLAG symbols are board-excluded, so KiCad drops them as nodes.
            nodes = {n for n in nodes if not n[0].startswith("#")}
            if name not in got:
                failures.append("net %s missing from KiCad's netlist" % name)
            elif got[name] != nodes:
                failures.append(
                    "net %s differs\n    intended: %s\n    kicad   : %s"
                    % (name, sorted(nodes), sorted(got[name])))

        # Pins carrying an explicit no-connect flag get an auto-named net.
        extra = {n for n in set(got) - set(want)
                 if not n.startswith("unconnected-")}
        if extra:
            failures.append("KiCad found unexpected nets: %s" % sorted(extra))
        deliberate = sorted(n for n in set(got) - set(want)
                            if n.startswith("unconnected-"))
        print("  deliberate no-connects: %s" % ", ".join(
            re.sub(r"unconnected-\((.*)\)", r"\1", n) for n in deliberate))

        print("\n  components in netlist : %d" % n_comp)
        print("  nets compared         : %d" % len(want))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("\nFAILED:")
        for f in failures:
            print("  - " + f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
