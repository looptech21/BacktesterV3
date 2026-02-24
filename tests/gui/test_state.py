from __future__ import annotations

from backtester.gui.state import JobState, RunSummary, init_app_state


def test_state_db_config_crud(tmp_path):
    state = init_app_state(tmp_path / "outputs")

    config_id = state.db.save_config(
        name="baseline",
        experiment_id="abc123",
        yaml_text="run_name: baseline\n",
    )
    assert config_id > 0

    configs = state.db.list_configs()
    assert len(configs) == 1
    assert configs[0]["name"] == "baseline"

    yaml_text = state.db.get_config_yaml(config_id)
    assert yaml_text is not None
    assert "run_name: baseline" in yaml_text


def test_state_db_run_history_and_job_registry(tmp_path):
    state = init_app_state(tmp_path / "outputs")

    summary = RunSummary(
        id=None,
        run_name="run_001",
        experiment_id="exp001",
        status="ok",
        run_dir=str(tmp_path / "outputs" / "run_001"),
        created_at="2026-02-22T00:00:00+00:00",
        finished_at="2026-02-22T00:01:00+00:00",
        metrics={"net_pnl": 123.45, "num_trades": 3},
        stages=[{"stage": "convert", "status": "ok", "duration_ms": 10}],
        error=None,
    )

    run_id = state.db.save_run(summary)
    assert run_id > 0

    runs = state.db.list_runs()
    assert len(runs) == 1
    assert runs[0].run_name == "run_001"
    assert runs[0].metrics["num_trades"] == 3

    state.set_selected_run_id(run_id)
    assert state.get_selected_run_id() == run_id
    assert state.db.get_ui_pref("selected_run_id") == str(run_id)

    job = JobState(job_id="job_1", run_name="run_001")
    state.add_job(job)
    assert state.has_running_job()
    state.update_job("job_1", status="running", stage="lean", message="running lean")
    state.append_job_event("job_1", stage="lean", message="running lean", fraction=0.4)
    updated = state.get_job("job_1")
    assert updated is not None
    assert updated.stage == "lean"
    assert len(updated.events) == 1
    state.clear_active_job("job_1")
    state.update_job("job_1", status="ok")
    assert not state.has_running_job()


def _make_run_summary(tmp_path, name: str, *, sweep_group_id: str | None = None, idx: int = 0) -> RunSummary:
    return RunSummary(
        id=None,
        run_name=name,
        experiment_id=f"exp_{name}",
        status="ok",
        run_dir=str(tmp_path / "outputs" / f"{name}_{idx}"),
        created_at="2026-02-22T00:00:00+00:00",
        finished_at="2026-02-22T00:01:00+00:00",
        metrics={"net_pnl": 100.0, "num_trades": 5},
        stages=[],
        error=None,
        sweep_group_id=sweep_group_id,
    )


def test_sweep_group_id_persisted(tmp_path):
    state = init_app_state(tmp_path / "outputs")
    summary = _make_run_summary(tmp_path, "sweep_001", sweep_group_id="grp_abc")
    run_id = state.db.save_run(summary)
    loaded = state.db.get_run(run_id)
    assert loaded is not None
    assert loaded.sweep_group_id == "grp_abc"


def test_sweep_group_id_none_when_not_set(tmp_path):
    state = init_app_state(tmp_path / "outputs")
    summary = _make_run_summary(tmp_path, "single_001")
    run_id = state.db.save_run(summary)
    loaded = state.db.get_run(run_id)
    assert loaded is not None
    assert loaded.sweep_group_id is None


def test_list_sweep_groups(tmp_path):
    state = init_app_state(tmp_path / "outputs")
    state.db.save_run(_make_run_summary(tmp_path, "s1", sweep_group_id="grp_a", idx=0))
    state.db.save_run(_make_run_summary(tmp_path, "s2", sweep_group_id="grp_a", idx=1))
    state.db.save_run(_make_run_summary(tmp_path, "s3", sweep_group_id="grp_b", idx=2))

    groups = state.db.list_sweep_groups()
    assert len(groups) == 2
    group_ids = {g["group_id"] for g in groups}
    assert group_ids == {"grp_a", "grp_b"}
    grp_a = next(g for g in groups if g["group_id"] == "grp_a")
    assert grp_a["run_count"] == 2


def test_list_runs_by_sweep_group(tmp_path):
    state = init_app_state(tmp_path / "outputs")
    state.db.save_run(_make_run_summary(tmp_path, "s1", sweep_group_id="grp_x", idx=0))
    state.db.save_run(_make_run_summary(tmp_path, "s2", sweep_group_id="grp_x", idx=1))
    state.db.save_run(_make_run_summary(tmp_path, "s3", sweep_group_id="grp_y", idx=2))
    state.db.save_run(_make_run_summary(tmp_path, "single", idx=3))

    grp_x_runs = state.db.list_runs_by_sweep_group("grp_x")
    assert len(grp_x_runs) == 2
    assert all(r.sweep_group_id == "grp_x" for r in grp_x_runs)
