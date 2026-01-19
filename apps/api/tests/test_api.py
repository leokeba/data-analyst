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

    delete_resp = client.delete(f"/projects/{project_id}/datasets/{dataset['id']}")
    assert delete_resp.status_code == 204

    delete_project_resp = client.delete(f"/projects/{project_id}")
    assert delete_project_resp.status_code == 204
