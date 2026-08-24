"""릴리즈 워크플로가 태그를 원격에 push 하는지 회귀 방지 (issue #10).

이 테스트는 `.github/workflows/release.yml` 을 읽어 YAML 로 파싱하고 구조에
단언한다. 텍스트/정규식 검색이 아니라 파서를 통해 주석·문자열을 코드와
구분한다.

이 파일은 저장소 파일을 **읽기만** 한다. 프로세스를 spawn 하지 않는다 —
`tests/test_repo_hygiene.py` 와 같은 관례.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


def _release_steps() -> list[dict]:
    assert WORKFLOW.exists(), f"missing workflow file: {WORKFLOW}"
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return doc["jobs"]["release"]["steps"]


def _step_index(steps: list[dict], predicate) -> int:
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    return -1


def test_workflow_file_parses_and_has_release_job():
    assert WORKFLOW.exists()
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["release"]["steps"]
    assert isinstance(steps, list)
    assert len(steps) > 0


def test_checkout_keeps_full_history_for_tags():
    steps = _release_steps()
    idx = _step_index(steps, lambda s: str(s.get("uses", "")).startswith("actions/checkout"))
    assert idx != -1, "no actions/checkout step found"
    assert steps[idx].get("with", {}).get("fetch-depth") == 0


def test_changesets_step_has_id_and_tag_publish():
    steps = _release_steps()
    idx = _step_index(steps, lambda s: str(s.get("uses", "")).startswith("changesets/action"))
    assert idx != -1, "no changesets/action step found"
    step = steps[idx]
    assert step.get("id") == "changesets"
    assert "changeset tag" in step.get("with", {}).get("publish", "")


def test_tag_push_step_runs_after_changesets():
    steps = _release_steps()
    changesets_idx = _step_index(steps, lambda s: str(s.get("uses", "")).startswith("changesets/action"))
    push_idx = _step_index(steps, lambda s: "git push origin --tags" in str(s.get("run", "")))
    assert changesets_idx != -1
    assert push_idx != -1, "no tag push step found"
    assert push_idx > changesets_idx


def test_tag_push_step_is_unconditional():
    steps = _release_steps()
    push_idx = _step_index(steps, lambda s: "git push origin --tags" in str(s.get("run", "")))
    assert push_idx != -1
    assert "if" not in steps[push_idx]


def test_tag_verification_step_gates_on_no_changesets():
    steps = _release_steps()
    push_idx = _step_index(steps, lambda s: "git push origin --tags" in str(s.get("run", "")))
    verify_idx = _step_index(
        steps,
        lambda s: "git ls-remote" in str(s.get("run", "")) and "--exit-code" in str(s.get("run", "")),
    )
    assert push_idx != -1
    assert verify_idx != -1, "no tag verification step found"
    assert verify_idx > push_idx
    if_str = str(steps[verify_idx].get("if", ""))
    assert if_str.replace(" ", "") == "steps.changesets.outputs.hasChangesets=='false'"


def test_no_step_swallows_failure():
    steps = _release_steps()
    for step in steps:
        assert step.get("continue-on-error") is not True, f"step swallows failure: {step.get('name')}"
    push_idx = _step_index(steps, lambda s: "git push origin --tags" in str(s.get("run", "")))
    verify_idx = _step_index(
        steps,
        lambda s: "git ls-remote" in str(s.get("run", "")) and "--exit-code" in str(s.get("run", "")),
    )
    assert push_idx != -1
    assert verify_idx != -1
    for idx in (push_idx, verify_idx):
        run_text = str(steps[idx].get("run", ""))
        assert "|| true" not in run_text
        assert "; true" not in run_text


def test_release_job_declares_no_npm_token():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "NPM_TOKEN" not in text


def test_changesets_action_stays_pinned_to_v1():
    steps = _release_steps()
    idx = _step_index(steps, lambda s: str(s.get("uses", "")).startswith("changesets/action"))
    assert idx != -1
    assert steps[idx].get("uses") == "changesets/action@v1"
