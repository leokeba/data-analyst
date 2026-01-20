from pathlib import Path


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_project_dataset_run_flow(client, tmp_path: Path):
    project = client.post("/projects", json={"name": "Test Project"}).json()
    project_id = project["id"]

    source_path = tmp_path / "sample.csv"
    source_path.write_text("a,b\n1,2\n3,4\n")

    dataset_resp = client.post(
        f"/projects/{project_id}/datasets",
        json={"name": "sample.csv", "source": str(source_path)},
    )
    assert dataset_resp.status_code == 201
    dataset = dataset_resp.json()

    download_resp = client.get(
        f"/projects/{project_id}/datasets/{dataset['id']}/download",
    )
    assert download_resp.status_code == 200
    assert download_resp.text.strip().startswith("a,b")

    preview_resp = client.get(
        f"/projects/{project_id}/datasets/{dataset['id']}/preview",
    )
    assert preview_resp.status_code == 200
    preview = preview_resp.json()
    assert preview["columns"] == ["a", "b"]
    assert preview["row_count"] == 2

    run_resp = client.post(
        f"/projects/{project_id}/runs",
        json={"dataset_id": dataset["id"], "type": "profile"},
    )
    assert run_resp.status_code == 201
    run = run_resp.json()
    assert run["status"] == "completed"

    artifacts_resp = client.get(
        f"/projects/{project_id}/artifacts",
        params={"run_id": run["id"]},
    )
    assert artifacts_resp.status_code == 200
    artifacts = artifacts_resp.json()
    assert len(artifacts) >= 1

    download_artifact_resp = client.get(
        f"/projects/{project_id}/artifacts/{artifacts[0]['id']}/download",
    )
    assert download_artifact_resp.status_code == 200

    delete_artifact_resp = client.delete(
        f"/projects/{project_id}/artifacts/{artifacts[0]['id']}",
    )
    assert delete_artifact_resp.status_code == 204

    delete_run_resp = client.delete(
        f"/projects/{project_id}/runs/{run['id']}",
    )
    assert delete_run_resp.status_code == 204

    report_resp = client.post(
        f"/projects/{project_id}/runs",
        json={"dataset_id": dataset["id"], "type": "report"},
    )
    assert report_resp.status_code == 201
    report_run = report_resp.json()
    assert report_run["status"] == "completed"

    report_artifacts_resp = client.get(
        f"/projects/{project_id}/artifacts",
        params={"run_id": report_run["id"]},
    )
    assert report_artifacts_resp.status_code == 200
    report_artifacts = report_artifacts_resp.json()
    artifact_types = {artifact["type"] for artifact in report_artifacts}
    assert "report_markdown" in artifact_types
    assert "report_html" in artifact_types

    delete_resp = client.delete(f"/projects/{project_id}/datasets/{dataset['id']}")
    assert delete_resp.status_code == 204

    delete_project_resp = client.delete(f"/projects/{project_id}")
    assert delete_project_resp.status_code == 204


def test_agent_run_executes_plan(client, tmp_path: Path):
    project = client.post("/projects", json={"name": "Agent Project"}).json()
    project_id = project["id"]

    source_path = tmp_path / "agent.csv"
    source_path.write_text("a,b\n1,2\n3,4\n")

    dataset_resp = client.post(
        f"/projects/{project_id}/datasets",
        json={"name": "agent.csv", "source": str(source_path)},
    )
    assert dataset_resp.status_code == 201
    dataset = dataset_resp.json()

    tools_resp = client.get(f"/projects/{project_id}/agent/tools")
    assert tools_resp.status_code == 200
    tool_names = {tool["name"] for tool in tools_resp.json()}
    assert "create_run" in tool_names
    assert "list_datasets" in tool_names
    assert "list_project_runs" in tool_names
    assert "list_artifacts" in tool_names

    plan_payload = {
        "objective": "Profile dataset",
        "steps": [
            {
                "id": "step-profile",
                "title": "Run profile",
                "description": "Create a profiling run",
                "tool": "create_run",
                "args": {"dataset_id": dataset["id"], "type": "profile"},
                "requires_approval": True,
            }
        ],
    }
    run_payload = {
        "plan": plan_payload,
        "approvals": {"step-profile": {"approved_by": "tester"}},
    }
    agent_run_resp = client.post(
        f"/projects/{project_id}/agent/runs",
        json=run_payload,
    )
    assert agent_run_resp.status_code == 201
    agent_run = agent_run_resp.json()
    assert agent_run["status"] == "completed"
    assert agent_run["log"][0]["status"] == "applied"

    run_id = agent_run["id"]
    agent_run_get = client.get(f"/projects/{project_id}/agent/runs/{run_id}")
    assert agent_run_get.status_code == 200

    list_runs_resp = client.get(f"/projects/{project_id}/agent/runs")
    assert list_runs_resp.status_code == 200
    assert list_runs_resp.headers.get("x-total-count")
