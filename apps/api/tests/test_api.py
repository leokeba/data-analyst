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

    delete_artifact_resp = client.delete(
        f"/projects/{project_id}/artifacts/{artifacts[0]['id']}",
    )
    assert delete_artifact_resp.status_code == 204

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
