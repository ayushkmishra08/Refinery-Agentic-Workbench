from workbench.core.plan import Plan, PlanStep, StepStatus
from workbench.core.request import TaskType
from workbench.orchestration.router import ROUTING_MATRIX


def test_routing_matrix_covers_every_task_type():
    assert set(ROUTING_MATRIX) == set(TaskType)


def test_plan_ready_steps_respects_dependencies():
    plan = Plan(plan_id="p", steps=[
        PlanStep(step_id="a", agent="procedure", goal="x"),
        PlanStep(step_id="b", agent="safety", goal="y", depends_on=["a"]),
    ])
    assert [s.step_id for s in plan.ready_steps()] == ["a"]
    plan.steps[0].status = StepStatus.DONE
    assert [s.step_id for s in plan.ready_steps()] == ["b"]
