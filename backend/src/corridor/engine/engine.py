"""The engine: single owner of world state, applies commands and publishes events.

Everything runs on the asyncio event loop thread and ``submit`` never awaits, so no two
commands interleave and the state needs no lock. Subscribers are called synchronously and
must not block (the WebSocket hub only enqueues).

Plans: while the mission is still ``planned``, every change to the cuts requests a new plan
with a new revision. The plan comes back later, on the event loop thread; a plan whose
revision is not the latest requested is dropped.

Flight: ``tick`` runs about twice per wall-clock second. It reports confirmed cuts starting
and ending (``jammer.restarted``), replans a plan whose take-off time has passed before launch,
moves the simulated drone (``drone.state``) and applies the safety rule of
``corridor.domain.safety``: when the drone can no longer keep its cut windows, it asks for a
fallback route from the drone position, using no cut, and switches the flight to it.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from typing import assert_never

from corridor.domain.commands import (
    CancelCut,
    Command,
    ConfirmCut,
    InjectDelay,
    LaunchMission,
    ScheduleCut,
    SetClock,
)
from corridor.domain.events import (
    ClockChanged,
    CutCancelled,
    CutConfirmed,
    CutScheduled,
    DroneState,
    Event,
    JammerRestarted,
    MissionStatusChanged,
    Notification,
    NotificationAction,
    PlanUpdated,
)
from corridor.domain.geo import LocalFrame
from corridor.domain.planner.plan import MissionPlan
from corridor.domain.safety import (
    Flight,
    cut_transitions,
    position_at,
    schedule_violation,
    takeoff_s,
)
from corridor.domain.scenario import Scenario
from corridor.domain.state import Cut, WorldState
from corridor.domain.zones import mission_discs
from corridor.engine.clock import SimClock
from corridor.engine.replan import Replanner

Subscriber = Callable[[Event], None]

PLAN_LEAD_WALL_S = 3.0
"""Wall-clock seconds a plan may take. A replan's earliest takeoff is that far ahead in
simulated time, so the route does not start in the past by the time it arrives."""


FLYABLE = ("approved", "baseline")
"""Plan statuses a drone may launch on. ``preview`` waits for the operator's confirmation."""


class CommandRejected(Exception):
    """The command breaks a domain rule. The message is shown to the sender."""


class Engine:
    def __init__(
        self, scenario: Scenario, clock: SimClock, replanner: Replanner | None = None
    ) -> None:
        """Without ``replanner``, nothing is planned."""
        self.scenario = scenario
        self.clock = clock
        self.state = WorldState.from_scenario(scenario)
        self.plan: PlanUpdated | None = None
        self.flight: Flight | None = None
        self.drone: DroneState | None = None
        self._replanner = replanner
        self._revision = 0
        self._awaiting: int | None = None
        self._subscribers: list[Subscriber] = []
        self._cut_ids = itertools.count(1)
        self._frame = LocalFrame(scenario.mission.launch)
        self._discs = mission_discs(scenario)
        self._last_tick_s = clock.now()

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def submit(self, command: Command) -> list[Event]:
        """Apply ``command`` and publish the resulting events.

        Raises CommandRejected, leaving the state unchanged.
        """
        events = self._apply(command)
        self._publish(events)
        if isinstance(command, ScheduleCut | ConfirmCut | CancelCut):
            self.request_plan()
        return events

    def request_plan(self) -> None:
        """Ask for a plan on the current cuts, unless the mission has left the ground."""
        if self._replanner is None or self.state.mission_status != "planned":
            return
        self._revision += 1
        self._awaiting = self._revision
        clock = self.clock.state()
        lead_s = 0.0 if clock.paused else PLAN_LEAD_WALL_S * clock.factor
        self._replanner.request(
            self._revision,
            list(self.state.cuts.values()),
            clock.sim_time_s + lead_s,
            self._plan_done,
        )

    def _plan_done(self, revision: int, plan: MissionPlan | None) -> None:
        if revision != self._revision or self.state.mission_status != "planned":
            return  # a newer plan is on its way, or the drone already flies
        self._awaiting = None
        if plan is None:
            pilot = self.scenario.mission.pilot_callsign
            self._publish([Notification(to=pilot, text="No route to the target within battery.")])
            return
        self.plan = PlanUpdated(
            revision=revision,
            status=plan.status,
            metrics=plan.metrics,
            baseline_route=plan.baseline_route,
            optimised_route=plan.optimised_route,
        )
        self._publish([self.plan])

    def tick(self) -> list[Event]:
        """Advance time-driven behaviour to the current simulated time."""
        now = self.clock.now()
        previous, self._last_tick_s = self._last_tick_s, now
        if now <= previous:
            return []
        events = self._cut_events(previous, now)
        stale = (
            self.state.mission_status == "planned"
            and self.plan is not None
            and self._awaiting is None
            and takeoff_s(self.plan.optimised_route) < now
        )
        if self.flight is not None:
            events += self._fly(now)
        self._publish(events)
        if stale:
            self.request_plan()
        return events

    def _cut_events(self, after: float, until: float) -> list[Event]:
        events: list[Event] = []
        label = self.scenario.clock_label
        for cut, kind in cut_transitions(list(self.state.cuts.values()), after, until):
            jammer = self.scenario.friendly_jammer(cut.jammer)
            operator = jammer.operator_callsign if jammer else cut.jammer
            if kind == "start":
                text = (
                    f"Cut {cut.jammer} now ({label(cut.start_s)}), restart at {label(cut.end_s)}."
                )
            else:
                events.append(JammerRestarted(jammer=cut.jammer, cut=cut.id))
                text = f"Restart {cut.jammer} now ({label(cut.end_s)})."
            events.append(Notification(to=operator, text=text))
        return events

    def _fly(self, now: float) -> list[Event]:
        flight = self.flight
        assert flight is not None
        mission = self.scenario.mission
        events: list[Event] = []
        if not flight.fallback_requested:
            cuts = list(self.state.cuts.values())
            zone = schedule_violation(flight, now, self._frame, self._discs, cuts)
            if zone is not None:
                flight.fallback_requested = True
                text = (
                    f"{mission.drone_id} would cross {zone} outside a confirmed cut "
                    f"(late {flight.delay_s:.0f} s). Computing a safe route."
                )
                events.append(Notification(to=mission.pilot_callsign, text=text))
                self._request_fallback(now)
        position, action = position_at(flight.route, flight.route_time(now))
        self.drone = DroneState(
            position=position,
            action=action,
            battery_used_pct=round(100 * flight.airborne_s(now) / mission.battery_max_s, 1),
            delay_s=round(flight.delay_s, 1),
            revision=flight.revision,
        )
        events.append(self.drone)
        if flight.arrived(now):
            self.flight = None
            self.state.mission_status = "arrived"
            text = (
                f"{mission.drone_id} on target ({self.scenario.clock_label(now)}), "
                f"battery used {self.drone.battery_used_pct:.0f}%."
            )
            events += [
                MissionStatusChanged(status="arrived"),
                Notification(to=mission.pilot_callsign, text=text),
            ]
        return events

    def _request_fallback(self, now: float) -> None:
        if self._replanner is None or self.flight is None:
            return
        self._revision += 1
        position = position_at(self.flight.route, self.flight.route_time(now))[0]
        self._replanner.request_fallback(self._revision, position, now, self._fallback_done)

    def _fallback_done(self, revision: int, plan: MissionPlan | None) -> None:
        if revision != self._revision or self.flight is None:
            return
        pilot = self.scenario.mission.pilot_callsign
        if plan is None:
            text = "No safe route from the drone position: keep the current one."
            self._publish([Notification(to=pilot, text=text)])
            return
        now = self.clock.now()
        self.plan = PlanUpdated(
            revision=revision,
            status=plan.status,
            metrics=plan.metrics,
            baseline_route=plan.baseline_route,
            optimised_route=plan.optimised_route,
        )
        self.flight = Flight(
            route=plan.optimised_route,
            revision=revision,
            air_before_s=self.flight.airborne_s(now),
        )
        label = self.scenario.clock_label
        text = (
            f"Safe route rev {revision}: on target {label(plan.optimised_route[-1].t)}, "
            f"battery {plan.metrics.optimised_battery_used_pct:.0f}% for this leg."
        )
        self._publish([self.plan, Notification(to=pilot, text=text)])

    def notify(self, to: str, text: str) -> None:
        """Feed message from an adapter (e.g. the route was sent to the pilot's ATAK)."""
        self._publish([Notification(to=to, text=text)])

    def _publish(self, events: list[Event]) -> None:
        for event in events:
            for subscriber in self._subscribers:
                subscriber(event)

    def _apply(self, command: Command) -> list[Event]:
        match command:
            case ScheduleCut():
                return self._schedule_cut(command)
            case CancelCut():
                return self._cancel_cut(command)
            case ConfirmCut():
                return self._confirm_cut(command)
            case LaunchMission():
                return self._launch_mission(command)
            case InjectDelay():
                return self._inject_delay(command)
            case SetClock():
                self.clock.set(factor=command.factor, paused=command.paused)
                return [ClockChanged(clock=self.clock.state())]
            case _:
                assert_never(command)

    def _schedule_cut(self, command: ScheduleCut) -> list[Event]:
        jammer = self.scenario.friendly_jammer(command.jammer)
        if jammer is None:
            raise CommandRejected(f"unknown friendly jammer {command.jammer}")
        if command.start_s < self.clock.now():
            raise CommandRejected("cut starts in the past")
        for other in self.state.cuts.values():
            if (
                other.jammer == jammer.id
                and command.start_s < other.end_s
                and other.start_s < command.end_s
            ):
                raise CommandRejected(f"overlaps cut {other.id}")
        cut = Cut(
            id=f"cut-{next(self._cut_ids)}",
            jammer=jammer.id,
            start_s=command.start_s,
            end_s=command.end_s,
            status="scheduled",
            requested_by=command.actor,
        )
        self.state.cuts[cut.id] = cut
        label = self.scenario.clock_label
        text = (
            f"Cut {jammer.id} {label(cut.start_s)} to {label(cut.end_s)} "
            f"requested by {command.actor}. Confirm?"
        )
        return [
            CutScheduled(cut=cut),
            Notification(
                to=jammer.operator_callsign,
                text=text,
                action=NotificationAction(command="cut.confirm", cut=cut.id),
            ),
        ]

    def _cancel_cut(self, command: CancelCut) -> list[Event]:
        if self.state.cuts.pop(command.cut, None) is None:
            raise CommandRejected(f"unknown cut {command.cut}")
        return [CutCancelled(cut=command.cut)]

    def _confirm_cut(self, command: ConfirmCut) -> list[Event]:
        cut = self.state.cuts.get(command.cut)
        if cut is None:
            raise CommandRejected(f"unknown cut {command.cut}")
        if cut.status != "scheduled":
            raise CommandRejected(f"cut {cut.id} is already {cut.status}")
        jammer = self.scenario.friendly_jammer(cut.jammer)
        if jammer is None or command.actor != jammer.operator_callsign:
            raise CommandRejected(f"only the operator of {cut.jammer} can confirm this cut")
        confirmed = cut.model_copy(update={"status": "confirmed"})
        self.state.cuts[cut.id] = confirmed
        return [CutConfirmed(cut=confirmed)]

    def _launch_mission(self, command: LaunchMission) -> list[Event]:
        """With a planner, the drone flies the current plan, which must be flyable."""
        if self.state.mission_status != "planned":
            raise CommandRejected(f"mission is already {self.state.mission_status}")
        if self._replanner is not None:
            if self.plan is None:
                raise CommandRejected("no plan yet")
            if self.plan.status not in FLYABLE:
                raise CommandRejected("the plan relies on a cut not confirmed yet")
            route = self.plan.optimised_route
            # Launched after the planned take-off: the flight starts late by the difference.
            late = max(0.0, self.clock.now() - takeoff_s(route))
            self.flight = Flight(route=route, revision=self.plan.revision, delay_s=late)
        self.state.mission_status = "flying"
        return [MissionStatusChanged(status="flying")]

    def _inject_delay(self, command: InjectDelay) -> list[Event]:
        if self.flight is None:
            raise CommandRejected("no drone in flight")
        self.flight.hold(self.clock.now(), command.delay_s)
        mission = self.scenario.mission
        text = f"{mission.drone_id} held {command.delay_s:.0f} s by {command.actor}."
        return [Notification(to=mission.pilot_callsign, text=text)]
