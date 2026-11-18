"""The command line of tools/sabotage.py."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor

from .cases import CASES
from .files import REPO
from .runner import run_case, run_tests


def main(description: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="run the cases whose name contains one of these")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()

    cases = [c for c in CASES if not args.names or any(n in c.name for n in args.names)]
    if args.list:
        for case in cases:
            print(case.name)
        return 0
    if not args.discover:
        empty = [c.name for c in CASES if not c.red]
        if empty:
            print(f"cases with no red test named: {empty}")
            return 1
        names = [c.name for c in CASES]
        if len(set(names)) != len(names):
            print("two cases share a name")
            return 1

    code, _, log = run_tests(REPO, [])
    if code != 0:
        print("The unedited tree is not green, so no red can be trusted:\n" + log[-3000:])
        return 1
    print(f"GREEN  the unedited tree passes the whole suite; {len(cases)} case(s) follow", flush=True)

    failed = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for case, ok, line in pool.map(lambda c: run_case(c, args.discover), cases):
            print(f"{'ok  ' if ok else 'FAIL'}  {case.name}: {line}", flush=True)
            failed += 0 if ok else 1
    print(f"{len(cases) - failed} of {len(cases)} case(s) went red as expected")
    return 1 if failed else 0
