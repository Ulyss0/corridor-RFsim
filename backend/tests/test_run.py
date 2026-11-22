import io
from pathlib import Path

import pytest

from corridor.domain.commands import LaunchMission, ScheduleCut
from corridor.domain.scenario import Scenario
from corridor.sim.cli import main, summary
from corridor.sim.log import (
    CommandRefused,
    EventLogged,
    LogEntry,
    RunEnded,
    RunStarted,
    read_log,
    write_entry,
)
from corridor.sim.runner import run
from corridor.sim.script import CommandScript, TimedCommand, load_script

BACKEND = Path(__file__).resolve().parent.parent
DEMO = BACKEND / "scenarios" / "demo.json"
TWO_FLIGHTS = BACKEND / "scenarios" / "scripts" / "demo_two_flights.json"


def _collect(scenario: Scenario, script: CommandScript, **options: float) -> list[LogEntry]:
    entries: list[LogEntry] = []
    run(scenario, script, entries.append, **options)
    return entries


def _launch_on_cut(*, confirm: bool) -> CommandScript:
    commands = [
        TimedCommand(
            at_s=0,
            command=ScheduleCut(actor="PILOT-1", jammer="J3", start_s=900, end_s=1380),
        ),
        TimedCommand(at_s=60, command=LaunchMission(actor="PILOT-1", mission="M1")),
    ]
    if confirm:
        confirm_cut = {"type": "cut.confirm", "actor": "CMD", "cut": "cut-1"}
        commands.insert(1, TimedCommand.model_validate({"at_s": 30, "command": confirm_cut}))
    return CommandScript(commands=commands)


def test_a_scripted_flight_arrives_and_the_run_stops(scenario: Scenario) -> None:
    entries = _collect(scenario, _launch_on_cut(confirm=True))
    end = entries[-1]
    assert isinstance(end, RunEnded)
    assert end.missions == {"M1": "arrived"}
    assert end.t < 3600  # stopped once every mission had arrived
    times = [e.t for e in entries if isinstance(e, EventLogged)]
    assert times == sorted(times)


def test_commands_are_sent_at_their_own_time(scenario: Scenario) -> None:
    entries = _collect(scenario, _launch_on_cut(confirm=True), step_s=7)
    launched = next(
        e
        for e in entries
        if isinstance(e, EventLogged)
        and e.event.type == "mission.status"
        and e.event.status == "flying"
    )
    assert launched.t == 60  # not 63, the next 7 s step


def test_a_refused_command_is_logged_and_the_run_goes_on(scenario: Scenario) -> None:
    entries = _collect(scenario, _launch_on_cut(confirm=False), until_s=300)
    refused = [e for e in entries if isinstance(e, CommandRefused)]
    assert len(refused) == 1
    assert refused[0].t == 60
    assert refused[0].command.type == "mission.launch"
    assert "not confirmed" in refused[0].reason
    end = entries[-1]
    assert isinstance(end, RunEnded)
    assert end.t == 300
    assert end.missions == {"M1": "planned"}


def test_the_same_inputs_give_the_same_log(scenario: Scenario) -> None:
    script = _launch_on_cut(confirm=True)
    assert _collect(scenario, script) == _collect(scenario, script)


def test_the_log_reads_back(scenario: Scenario, tmp_path: Path) -> None:
    entries = _collect(scenario, _launch_on_cut(confirm=False), until_s=120)
    path = tmp_path / "run.jsonl"
    with path.open("w", encoding="utf-8") as out:
        for entry in entries:
            write_entry(out, entry)
    assert list(read_log(path)) == entries


def test_bad_options_are_refused(scenario: Scenario) -> None:
    with pytest.raises(ValueError, match="step_s"):
        run(scenario, CommandScript(), lambda _: None, step_s=0)


def test_cli_runs_the_demo_script_reproducibly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logs = [tmp_path / "a.jsonl", tmp_path / "b.jsonl"]
    for log in logs:
        assert main(["run", str(DEMO), "--script", str(TWO_FLIGHTS), "--log", str(log)]) == 0
    assert logs[0].read_bytes() == logs[1].read_bytes()
    first = next(read_log(logs[0]))
    assert isinstance(first, RunStarted)
    assert first.script == "demo_two_flights.json"
    assert "M1 arrived, M2 arrived" in capsys.readouterr().err

    assert main(["summary", str(logs[0])]) == 0
    printed = capsys.readouterr().out
    assert "  M1: arrived" in printed
    assert "  M2: arrived" in printed


def test_the_sample_script_loads() -> None:
    script = load_script(TWO_FLIGHTS)
    assert [c.command.type for c in script.in_order()] == [
        "cut.schedule",
        "cut.confirm",
        "mission.launch",
        "mission.launch",
    ]


def test_summary_lists_refusals() -> None:
    entries: list[LogEntry] = [
        CommandRefused(
            t=60,
            command=LaunchMission(actor="PILOT-1", mission="M1"),
            reason="the plan relies on a cut not confirmed yet",
        ),
        RunEnded(t=300, missions={"M1": "planned"}),
    ]
    text = "\n".join(summary(entries))
    assert "refused at 60 s: mission.launch: the plan relies on a cut not confirmed yet" in text
    assert "  M1: planned" in text


def test_write_entry_is_one_line() -> None:
    out = io.StringIO()
    write_entry(out, RunEnded(t=1, missions={}))
    assert out.getvalue().count("\n") == 1
