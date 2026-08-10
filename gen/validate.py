#!/usr/bin/env python3
"""
Validate the generated schematic with KiCad itself.

  1. every sheet must load standalone (catches malformed symbol caches)
  2. the whole hierarchy must load and produce a netlist
  3. KiCad's extracted connectivity must match netlist.txt exactly

Exits non-zero on any discrepancy.  Requires kicad-cli.

Usage: python3 gen/validate.py
"""

import glob
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


def find_kicad_cli():
    """kicad-cli on PATH, or in the usual install location per platform.

    The Windows installer does not add KiCad to PATH, so this looks there too.
    """
    found = shutil.which("kicad-cli")
    if found:
        return found
    candidates = []
    for pat in (r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\*\bin\kicad-cli.exe",
                "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
                "/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli"):
        candidates.extend(glob.glob(pat))
    return sorted(candidates)[-1] if candidates else None


CLI = None  # resolved in main()


def has_erc():
    """kicad-cli gained `sch erc` in 8.0; 7.x only has `sch export`."""
    res = subprocess.run([CLI, "sch", "--help"], capture_output=True, text=True)
    return "erc" in (res.stdout + res.stderr)


def run_erc(sch, workdir):
    """Return (violation_list, error_string). Empty list + '' means clean."""
    out = os.path.join(workdir, "erc.json")
    res = subprocess.run(
        [CLI, "sch", "erc", "--output", out, "--format", "json",
         "--severity-error", "--severity-warning", sch],
        capture_output=True, text=True)
    if not os.path.exists(out):
        return None, (res.stdout + res.stderr).strip() or "no ERC report produced"
    try:
        import json
        report = json.load(open(out, encoding="utf-8"))
    except Exception as exc:                       # noqa: BLE001
        return None, "could not parse ERC report: %s" % exc

    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "violations" and isinstance(val, list):
                    found.extend(val)
                else:
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(report)
    return found, ""


def kicad_netlist(sch, workdir):
    out = os.path.join(workdir, "n.net")
    res = subprocess.run(
        [CLI, "sch", "export", "netlist", "--output", out, sch],
        capture_output=True, text=True)
    if res.returncode != 0:
        return None, (res.stdout + res.stderr).strip()
    return out, ""


def main():
    global CLI
    CLI = find_kicad_cli()
    if CLI is None:
        print("kicad-cli not found. Install KiCad, or add its bin directory to\n"
              "PATH. Typical locations:\n"
              r"  Windows: C:\Program Files\KiCad\9.0\bin" "\n"
              "  macOS:   /Applications/KiCad/KiCad.app/Contents/MacOS\n"
              "  Linux:   /usr/bin")
        return 2
    print("kicad-cli   : %s" % CLI)

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

        # 4. ERC, where the installed kicad-cli can do it (8.0 and later).
        if not has_erc():
            print("  erc                   : SKIPPED — this kicad-cli has no "
                  "`sch erc` subcommand (added in 8.0). Run ERC in the GUI.")
        else:
            violations, err = run_erc(ROOT, tmp)
            if violations is None:
                failures.append("ERC could not be run: " + err)
            elif violations:
                print("  erc                   : %d violation(s)" % len(violations))
                for v in violations[:40]:
                    sev = v.get("severity", "?")
                    desc = v.get("description") or v.get("type", "?")
                    failures.append("ERC %s: %s" % (sev, desc))
            else:
                print("  erc                   : clean")
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
