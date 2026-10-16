from datetime import UTC, datetime

from hoy_api.worker import CycleOutcome, run_scheduler


def test_scheduler_sleeps_until_0400_then_runs_one_cycle() -> None:
    now = {"value": datetime(2026, 10, 16, 2, 0, tzinfo=UTC)}
    slept: list[float] = []
    cycles: list[CycleOutcome] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)

    def cycle() -> CycleOutcome:
        outcome = CycleOutcome(status="succeeded")
        cycles.append(outcome)
        return outcome

    completed = run_scheduler(
        cycle=cycle,
        clock=lambda: now["value"],
        sleep=sleep,
        should_stop=lambda: len(cycles) >= 1,
        max_cycles=1,
    )

    assert completed == 1
    assert sum(slept) == 3600.0  # 02:00 UTC to 03:00 UTC (04:00 WEST)
    assert max(slept) <= 30.0  # slept in short steps so shutdown stays responsive
