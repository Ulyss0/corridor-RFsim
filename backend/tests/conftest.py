from pathlib import Path
from typing import Any

import pytest

from corridor.domain.scenario import Scenario, load_scenario

ROOT = Path(__file__).resolve().parent.parent


def demo() -> Scenario:
    """M1 with J3, and M2 to the south with J4. Each jammer concerns one mission only."""
    return load_scenario(ROOT / "scenarios" / "demo.json")


def single_mission(scenario: Scenario) -> Scenario:
    """``scenario`` with M1 and the threats around it only."""
    data: dict[str, Any] = scenario.model_dump(mode="json")
    data["missions"] = [m for m in data["missions"] if m["id"] == "M1"]
    data["friendly_jammers"] = [j for j in data["friendly_jammers"] if j["id"] == "J3"]
    return Scenario.model_validate(data)


@pytest.fixture
def scenario() -> Scenario:
    """The demo reduced to M1: J3 between pilot and target, R1 and E1 to the sides."""
    return single_mission(demo())


@pytest.fixture
def two_missions() -> Scenario:
    return demo()


@pytest.fixture(scope="module")
def scenario_module() -> Scenario:
    """``scenario``, shared by the tests of a module."""
    return single_mission(demo())
