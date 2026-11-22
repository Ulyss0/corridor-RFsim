"""corridor-run: headless runs and their logs.

corridor-run run scenarios/demo.json --script scenarios/demo.script.json --log run.jsonl
corridor-run summary run.jsonl
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from corridor.domain.scenario import load_scenario
from corridor.sim.log import (
    CommandRefused,
    EventLogged,
    LogEntry,
    RunEnded,
    RunStarted,
    read_log,
    write_entry,
)
from corridor.sim.runner import DEFAULT_STEP_S, DEFAULT_UNTIL_S, run
from corridor.sim.script import CommandScript, file_sha256, load_script


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corridor-run", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    go = commands.add_parser("run", help="run a scenario and a command script, write the log")
    go.add_argument("scenario", type=Path)
    go.add_argument("--script", type=Path, help="command script (JSON); none: no human action")
    go.add_argument("--log", type=Path, help="log file (JSON Lines); default: standard output")
    go.add_argument("--cell-m", type=float, default=100.0, help="planning grid cell size")
    go.add_argument("--step-s", type=float, default=DEFAULT_STEP_S, help="simulated step")
    go.add_argument("--until-s", type=float, default=DEFAULT_UNTIL_S, help="last simulated time")

    show = commands.add_parser("summary", help="what a log says, in a few lines")
    show.add_argument("log", type=Path)

    args = parser.parse_args(argv)
    if args.command == "summary":
        print("\n".join(summary(list(read_log(args.log)))))
        return 0
    return _run(args)


def _run(args: argparse.Namespace) -> int:
    scenario = load_scenario(args.scenario)
    script = load_script(args.script) if args.script else CommandScript()
    out = args.log.open("w", encoding="utf-8") if args.log else sys.stdout
    try:
        write_entry(
            out,
            RunStarted(
                scenario=args.scenario.name,
                scenario_sha256=file_sha256(args.scenario),
                script=args.script.name if args.script else None,
                script_sha256=file_sha256(args.script) if args.script else None,
                cell_m=args.cell_m,
                step_s=args.step_s,
                until_s=args.until_s,
            ),
        )
        result = run(
            scenario,
            script,
            lambda entry: write_entry(out, entry),
            cell_m=args.cell_m,
            step_s=args.step_s,
            until_s=args.until_s,
        )
    finally:
        if args.log:
            out.close()
    if args.log:
        states = ", ".join(f"{m} {s}" for m, s in result.missions.items())
        print(
            f"{args.log}: t = {result.end_s:.0f} s, {result.events} events, "
            f"{result.rejected} refused; {states}",
            file=sys.stderr,
        )
    return 0


def summary(entries: list[LogEntry]) -> list[str]:
    """Run, mission outcomes, event counts by type, refused commands."""
    lines: list[str] = []
    events: Counter[str] = Counter()
    for entry in entries:
        match entry:
            case RunStarted():
                script = entry.script or "no script"
                lines.append(f"Run of {entry.scenario} with {script}, cells {entry.cell_m:g} m")
            case EventLogged():
                events[entry.event.type] += 1
            case CommandRefused():
                lines.append(f"  refused at {entry.t:g} s: {entry.command.type}: {entry.reason}")
            case RunEnded():
                lines.append(f"Ended at {entry.t:g} s")
                lines += [f"  {m}: {s}" for m, s in entry.missions.items()]
    lines.append("Events: " + ", ".join(f"{t} {n}" for t, n in sorted(events.items())))
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
