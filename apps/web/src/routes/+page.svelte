<svelte:head>
	<title>Data Analyst Control Plane</title>
</svelte:head>

<script lang="ts">
	import { onMount } from "svelte";

	type Project = {
		id: string;
		name: string;
		created_at: string;
		workspace_path: string;
	};

	let projects: Project[] = [];
	let loading = true;
	let error = "";
	let newProjectName = "";
	let isCreating = false;
	let createError = "";
	let projectActionError = "";
	let deletingProjectId = "";
	let selectedProjectId = "";
	let uploadFile: File | null = null;
	let uploadError = "";
	let uploadMessage = "";
	let isUploading = false;
	let newDatasetName = "";
	let newDatasetSource = "";
	let isCreatingDataset = false;
	let createDatasetError = "";
	let datasets: Dataset[] = [];
	let datasetError = "";
	let datasetsLoading = false;
	let datasetActionError = "";
	let deletingDatasetId = "";
	let selectedDatasetId = "";
	let runType: RunType = "profile";
	let runMessage = "";
	let runError = "";
	let isRunning = false;
	let runs: Run[] = [];
	let runsLoading = false;
	let runsError = "";
	let runActionError = "";
	let deletingRunId = "";
	let artifacts: Artifact[] = [];
	let artifactsLoading = false;
	let artifactsError = "";
	let selectedRunId = "";
	let previewArtifactId = "";
	let previewContent = "";
	let previewError = "";
	let previewLoading = false;
	let artifactTypeFilter = "all";
	let artifactSearch = "";

	const apiBase = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

	type Dataset = {
		id: string;
		project_id: string;
		name: string;
		source: string;
		created_at: string;
		stats?: {
			row_count?: number;
			column_count?: number;
			file_size_bytes?: number;
		};
		schema_snapshot?: {
			columns?: { name: string; index: number }[];
		};
	};

	type RunType = "ingest" | "profile" | "analysis" | "report";

	type Run = {
		id: string;
		project_id: string;
		dataset_id: string;
		type: RunType;
		status: string;
		started_at: string;
		finished_at: string | null;
	};

	type Artifact = {
		id: string;
		run_id: string;
		type: string;
		path: string;
		mime_type: string;
		size: number;
	};

	onMount(async () => {
		await loadProjects();
	});

	const loadProjects = async () => {
		loading = true;
		error = "";
		try {
			const response = await fetch(`${apiBase}/projects`);
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			projects = await response.json();
		} catch (err) {
			error = err instanceof Error ? err.message : "Failed to load projects";
		} finally {
			loading = false;
		}
	};

	const createProject = async () => {
		if (!newProjectName.trim()) {
			createError = "Project name is required.";
			return;
		}
		isCreating = true;
		createError = "";
		projectActionError = "";
		try {
			const response = await fetch(`${apiBase}/projects`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ name: newProjectName.trim() })
			});
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			const created = await response.json();
			newProjectName = "";
			if (!selectedProjectId) {
				selectedProjectId = created.id;
				await loadDatasets(created.id);
				await loadRuns(created.id);
				await loadArtifacts(created.id);
			}
			await loadProjects();
		} catch (err) {
			createError = err instanceof Error ? err.message : "Failed to create project";
		} finally {
			isCreating = false;
		}
	};

	const deleteProject = async (projectId: string) => {
		projectActionError = "";
		deletingProjectId = projectId;
		try {
			const response = await fetch(`${apiBase}/projects/${projectId}`, {
				method: "DELETE"
			});
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			if (selectedProjectId === projectId) {
				selectedProjectId = "";
				selectedDatasetId = "";
				datasets = [];
				runs = [];
				artifacts = [];
			}
			await loadProjects();
		} catch (err) {
			projectActionError =
				err instanceof Error ? err.message : "Failed to delete project";
		} finally {
			deletingProjectId = "";
		}
	};

	const loadDatasets = async (projectId: string) => {
		datasetsLoading = true;
		datasetError = "";
		try {
			const response = await fetch(`${apiBase}/projects/${projectId}/datasets`);
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			datasets = await response.json();
		} catch (err) {
			datasetError = err instanceof Error ? err.message : "Failed to load datasets";
		} finally {
			datasetsLoading = false;
		}
	};

	const loadRuns = async (projectId: string) => {
		runsLoading = true;
		runsError = "";
		try {
			const response = await fetch(`${apiBase}/projects/${projectId}/runs`);
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			runs = await response.json();
		} catch (err) {
			runsError = err instanceof Error ? err.message : "Failed to load runs";
		} finally {
			runsLoading = false;
		}
	};

	const loadArtifacts = async (projectId: string, runId?: string) => {
		artifactsLoading = true;
		artifactsError = "";
		try {
			const url = new URL(`${apiBase}/projects/${projectId}/artifacts`);
			if (runId) {
				url.searchParams.set("run_id", runId);
			}
			const response = await fetch(url.toString());
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			artifacts = await response.json();
		} catch (err) {
			artifactsError = err instanceof Error ? err.message : "Failed to load artifacts";
		} finally {
			artifactsLoading = false;
		}
	};

	const handleProjectSelection = async (value: string) => {
		selectedProjectId = value;
		selectedDatasetId = "";
		if (value) {
			await loadDatasets(value);
			await loadRuns(value);
			selectedRunId = "";
			await loadArtifacts(value);
		} else {
			datasets = [];
			runs = [];
			artifacts = [];
			selectedRunId = "";
		}
	};

	const uploadDataset = async () => {
		uploadError = "";
		uploadMessage = "";
		if (!selectedProjectId) {
			uploadError = "Select a project first.";
			return;
		}
		if (!uploadFile) {
			uploadError = "Choose a file to upload.";
			return;
		}
		isUploading = true;
		try {
			const formData = new FormData();
			formData.append("file", uploadFile);
			const response = await fetch(
				`${apiBase}/projects/${selectedProjectId}/datasets/upload`,
				{
					method: "POST",
					body: formData
				}
			);
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			uploadMessage = "Dataset uploaded.";
			uploadFile = null;
			await loadDatasets(selectedProjectId);
		} catch (err) {
			uploadError = err instanceof Error ? err.message : "Upload failed";
		} finally {
			isUploading = false;
		}
	};

	const createDataset = async () => {
		createDatasetError = "";
		if (!selectedProjectId) {
			createDatasetError = "Select a project first.";
			return;
		}
		if (!newDatasetName.trim() || !newDatasetSource.trim()) {
			createDatasetError = "Name and source are required.";
			return;
		}
		isCreatingDataset = true;
		try {
			const response = await fetch(`${apiBase}/projects/${selectedProjectId}/datasets`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					name: newDatasetName.trim(),
					source: newDatasetSource.trim()
				})
			});
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			newDatasetName = "";
			newDatasetSource = "";
			await loadDatasets(selectedProjectId);
		} catch (err) {
			createDatasetError =
				err instanceof Error ? err.message : "Failed to create dataset";
		} finally {
			isCreatingDataset = false;
		}
	};

	const deleteDataset = async (datasetId: string) => {
		if (!selectedProjectId) {
			datasetActionError = "Select a project first.";
			return;
		}
		datasetActionError = "";
		deletingDatasetId = datasetId;
		try {
			const response = await fetch(
				`${apiBase}/projects/${selectedProjectId}/datasets/${datasetId}`,
				{ method: "DELETE" }
			);
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			if (selectedDatasetId === datasetId) {
				selectedDatasetId = "";
			}
			await loadDatasets(selectedProjectId);
			await loadRuns(selectedProjectId);
			await loadArtifacts(selectedProjectId);
		} catch (err) {
			datasetActionError =
				err instanceof Error ? err.message : "Failed to delete dataset";
		} finally {
			deletingDatasetId = "";
		}
	};

	const createRun = async () => {
		runMessage = "";
		runError = "";
		runActionError = "";
		if (!selectedProjectId || !selectedDatasetId) {
			runError = "Select a project and dataset.";
			return;
		}
		isRunning = true;
		try {
			const response = await fetch(`${apiBase}/projects/${selectedProjectId}/runs`, {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ dataset_id: selectedDatasetId, type: runType })
			});
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			runMessage = "Run completed (stub).";
			await loadRuns(selectedProjectId);
			await loadArtifacts(selectedProjectId, selectedRunId || undefined);
			await loadDatasets(selectedProjectId);
		} catch (err) {
			runError = err instanceof Error ? err.message : "Run failed";
		} finally {
			isRunning = false;
		}
	};

	const deleteRun = async (runId: string) => {
		if (!selectedProjectId) {
			runActionError = "Select a project first.";
			return;
		}
		runActionError = "";
		deletingRunId = runId;
		try {
			const response = await fetch(
				`${apiBase}/projects/${selectedProjectId}/runs/${runId}`,
				{ method: "DELETE" }
			);
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			if (selectedRunId === runId) {
				selectedRunId = "";
				previewArtifactId = "";
				previewContent = "";
				previewError = "";
			}
			await loadRuns(selectedProjectId);
			await loadArtifacts(selectedProjectId, selectedRunId || undefined);
		} catch (err) {
			runActionError = err instanceof Error ? err.message : "Failed to delete run";
		} finally {
			deletingRunId = "";
		}
	};

	const datasetNameById = (datasetId: string) => {
		const dataset = datasets.find((item) => item.id === datasetId);
		return dataset ? dataset.name : datasetId;
	};

	const runById = (runId: string) => runs.find((item) => item.id === runId);

	$: artifactTypes = Array.from(new Set(artifacts.map((item) => item.type))).sort();
	$: filteredArtifacts = artifacts.filter((artifact) => {
		const matchesType = artifactTypeFilter === "all" || artifact.type === artifactTypeFilter;
		const haystack = `${artifact.type} ${artifact.path} ${artifact.run_id}`.toLowerCase();
		const matchesSearch = !artifactSearch.trim() || haystack.includes(artifactSearch.trim().toLowerCase());
		return matchesType && matchesSearch;
	});

	const selectRun = async (runId: string) => {
		selectedRunId = runId;
		previewArtifactId = "";
		previewContent = "";
		previewError = "";
		if (selectedProjectId) {
			await loadArtifacts(selectedProjectId, runId);
		}
	};

	const clearRunFilter = async () => {
		selectedRunId = "";
		previewArtifactId = "";
		previewContent = "";
		previewError = "";
		if (selectedProjectId) {
			await loadArtifacts(selectedProjectId);
		}
	};

	const previewArtifact = async (artifactId: string) => {
		if (!selectedProjectId) {
			previewError = "Select a project first.";
			return;
		}
		previewLoading = true;
		previewError = "";
		previewContent = "";
		previewArtifactId = artifactId;
		try {
			const response = await fetch(
				`${apiBase}/projects/${selectedProjectId}/artifacts/${artifactId}/download`
			);
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			const contentType = response.headers.get("content-type") ?? "";
			const text = await response.text();
			if (contentType.includes("application/json")) {
				try {
					previewContent = JSON.stringify(JSON.parse(text), null, 2);
				} catch {
					previewContent = text;
				}
			} else {
				previewContent = text.slice(0, 4000);
			}
		} catch (err) {
			previewError = err instanceof Error ? err.message : "Preview failed";
		} finally {
			previewLoading = false;
		}
	};
</script>

<main class="page">
	<section class="hero">
		<p class="eyebrow">data-analyst</p>
		<h1>Control plane</h1>
		<p class="lede">
			Manage projects, datasets, runs, and reports from a single place.
		</p>
	</section>

	<section class="grid">
		<div class="card">
			<h2>Projects</h2>
			<p>Create isolated workspaces and environments.</p>
			<div class="form">
				<input
					placeholder="Project name"
					bind:value={newProjectName}
					disabled={isCreating}
				/>
				<button on:click={createProject} disabled={isCreating}>
					{isCreating ? "Creating…" : "Create"}
				</button>
			</div>
			{#if createError}
				<p class="error">{createError}</p>
			{/if}
			{#if loading}
				<p class="muted">Loading projects…</p>
			{:else if error}
				<p class="error">{error}</p>
			{:else if projects.length === 0}
				<p class="muted">No projects yet.</p>
			{:else}
				<ul>
					{#each projects as project}
						<li>
							<strong>{project.name}</strong>
							<span>{project.workspace_path}</span>
							<button
								class="danger"
								on:click={() => deleteProject(project.id)}
								disabled={deletingProjectId === project.id}
							>
								{deletingProjectId === project.id ? "Deleting…" : "Delete"}
							</button>
						</li>
					{/each}
				</ul>
			{/if}
			{#if projectActionError}
				<p class="error">{projectActionError}</p>
			{/if}
		</div>
		<div class="card">
			<h2>Datasets</h2>
			<p>Track ingestion sources, profiling, and quality checks.</p>
			<div class="form">
				<select bind:value={selectedProjectId} disabled={isUploading} on:change={(event) => handleProjectSelection((event.target as HTMLSelectElement).value)}>
					<option value="">Select project</option>
					{#each projects as project}
						<option value={project.id}>{project.name}</option>
					{/each}
				</select>
				<input
					type="file"
					on:change={(event) => {
						const target = event.currentTarget as HTMLInputElement;
						uploadFile = target.files ? target.files[0] : null;
					}}
					disabled={isUploading}
				/>
				<button on:click={uploadDataset} disabled={isUploading}>
					{isUploading ? "Uploading…" : "Upload"}
				</button>
			</div>
			<div class="form">
				<input
					placeholder="Dataset name"
					bind:value={newDatasetName}
					disabled={isCreatingDataset}
				/>
				<input
					placeholder="Source path or URL"
					bind:value={newDatasetSource}
					disabled={isCreatingDataset}
				/>
				<button on:click={createDataset} disabled={isCreatingDataset}>
					{isCreatingDataset ? "Saving…" : "Add source"}
				</button>
			</div>
			{#if datasetsLoading}
				<p class="muted">Loading datasets…</p>
			{:else if datasetError}
				<p class="error">{datasetError}</p>
			{:else if selectedProjectId && datasets.length === 0}
				<p class="muted">No datasets yet.</p>
			{:else if datasets.length > 0}
				<ul>
					{#each datasets as dataset}
						<li>
							<strong>{dataset.name}</strong>
							<span>{dataset.source}</span>
							{#if dataset.stats}
								<span>Rows: {dataset.stats.row_count ?? "—"}</span>
								<span>Columns: {dataset.stats.column_count ?? "—"}</span>
							{/if}
							{#if dataset.schema_snapshot?.columns?.length}
								<span>
									Columns: {dataset.schema_snapshot.columns.map((col) => col.name).join(", ")}
								</span>
							{/if}
							<button
								class="danger"
								on:click={() => deleteDataset(dataset.id)}
								disabled={deletingDatasetId === dataset.id}
							>
								{deletingDatasetId === dataset.id ? "Deleting…" : "Delete"}
							</button>
						</li>
					{/each}
				</ul>
			{/if}
			{#if uploadError}
				<p class="error">{uploadError}</p>
			{/if}
			{#if uploadMessage}
				<p class="success">{uploadMessage}</p>
			{/if}
			{#if createDatasetError}
				<p class="error">{createDatasetError}</p>
			{/if}
			{#if datasetActionError}
				<p class="error">{datasetActionError}</p>
			{/if}
		</div>
		<div class="card">
			<h2>Runs</h2>
			<p>Monitor profiling, analysis, and report runs.</p>
			<div class="form">
				<select bind:value={selectedDatasetId} disabled={isRunning}>
					<option value="">Select dataset</option>
					{#each datasets as dataset}
						<option value={dataset.id}>{dataset.name}</option>
					{/each}
				</select>
				<select bind:value={runType} disabled={isRunning}>
					<option value="ingest">ingest</option>
					<option value="profile">profile</option>
					<option value="analysis">analysis</option>
					<option value="report">report</option>
				</select>
				<button on:click={createRun} disabled={isRunning}>
					{isRunning ? "Queueing…" : "Queue run"}
				</button>
			</div>
			{#if runError}
				<p class="error">{runError}</p>
			{/if}
			{#if runMessage}
				<p class="success">{runMessage}</p>
			{/if}
			{#if runActionError}
				<p class="error">{runActionError}</p>
			{/if}
			{#if runsLoading}
				<p class="muted">Loading runs…</p>
			{:else if runsError}
				<p class="error">{runsError}</p>
			{:else if selectedProjectId && runs.length === 0}
				<p class="muted">No runs yet.</p>
			{:else if runs.length > 0}
				<ul>
					{#each runs as run}
						<li>
							<strong>{run.type} · {run.status}</strong>
							<span>Dataset: {datasetNameById(run.dataset_id)}</span>
							<span>Started: {new Date(run.started_at).toLocaleString()}</span>
							{#if run.finished_at}
								<span>Finished: {new Date(run.finished_at).toLocaleString()}</span>
							{/if}
							<button
								class="secondary"
								on:click={() => selectRun(run.id)}
								disabled={selectedRunId === run.id}
							>
								{selectedRunId === run.id ? "Selected" : "View"}
							</button>
							<button
								class="danger"
								on:click={() => deleteRun(run.id)}
								disabled={deletingRunId === run.id}
							>
								{deletingRunId === run.id ? "Deleting…" : "Delete"}
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
		<div class="card">
			<h2>Reports</h2>
			<p>Publish markdown, HTML, and PDF outputs.</p>
			<div class="form">
				{#if selectedRunId}
					<span class="tag">Filtered by run: {selectedRunId}</span>
					<button class="secondary" on:click={clearRunFilter}>Show all</button>
				{/if}
			</div>
			<div class="form">
				<select bind:value={artifactTypeFilter}>
					<option value="all">All types</option>
					{#each artifactTypes as type}
						<option value={type}>{type}</option>
					{/each}
				</select>
				<input
					placeholder="Search artifacts"
					bind:value={artifactSearch}
				/>
			</div>
			{#if selectedRunId}
				{#if runById(selectedRunId)}
					<div class="summary">
						<strong>Run details</strong>
						<span>Type: {runById(selectedRunId)?.type}</span>
						<span>Status: {runById(selectedRunId)?.status}</span>
						<span>Dataset: {datasetNameById(runById(selectedRunId)?.dataset_id ?? "")}</span>
					</div>
				{/if}
			{/if}
			{#if artifactsLoading}
				<p class="muted">Loading artifacts…</p>
			{:else if artifactsError}
				<p class="error">{artifactsError}</p>
			{:else if selectedProjectId && artifacts.length === 0}
				<p class="muted">No artifacts yet.</p>
			{:else if filteredArtifacts.length === 0}
				<p class="muted">No artifacts match the filters.</p>
			{:else if filteredArtifacts.length > 0}
				<ul>
					{#each filteredArtifacts as artifact}
						<li>
							<strong>{artifact.type}</strong>
							<span>Run: {artifact.run_id}</span>
							<span>{artifact.path}</span>
							<span>Type: {artifact.mime_type}</span>
							<span>Size: {artifact.size} bytes</span>
							{#if selectedProjectId}
								<a
									class="link"
									href={`${apiBase}/projects/${selectedProjectId}/artifacts/${artifact.id}/download`}
									target="_blank"
									rel="noreferrer"
								>
									Download
								</a>
								<button
									class="secondary"
									on:click={() => previewArtifact(artifact.id)}
									disabled={previewLoading && previewArtifactId === artifact.id}
								>
									{previewLoading && previewArtifactId === artifact.id
										? "Loading…"
										: "Preview"}
								</button>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
			{#if previewError}
				<p class="error">{previewError}</p>
			{/if}
			{#if previewContent}
				<pre class="preview">{previewContent}</pre>
			{/if}
		</div>
	</section>
</main>

<style>
	:global(body) {
		margin: 0;
		font-family: "Inter", system-ui, sans-serif;
		color: #0f172a;
		background: #f8fafc;
	}

	.page {
		min-height: 100vh;
		padding: 64px 8vw;
		display: grid;
		gap: 48px;
	}

	.hero {
		display: grid;
		gap: 16px;
	}

	.eyebrow {
		text-transform: uppercase;
		letter-spacing: 0.14em;
		font-size: 12px;
		color: #64748b;
	}

	.lede {
		font-size: 18px;
		color: #475569;
		max-width: 640px;
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 20px;
	}

	.card {
		background: white;
		border-radius: 16px;
		padding: 20px;
		box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
		border: 1px solid #e2e8f0;
	}

	.card h2 {
		margin: 0 0 8px;
		font-size: 18px;
	}

	.card p {
		margin: 0;
		color: #64748b;
	}

	.form {
		margin-top: 16px;
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}

	.form input {
		flex: 1 1 180px;
		padding: 10px 12px;
		border-radius: 10px;
		border: 1px solid #cbd5f5;
		font-size: 14px;
	}

	.form select {
		flex: 1 1 140px;
		padding: 10px 12px;
		border-radius: 10px;
		border: 1px solid #cbd5f5;
		font-size: 14px;
		background: white;
	}

	.form button {
		padding: 10px 16px;
		border-radius: 10px;
		border: none;
		background: #1e293b;
		color: white;
		font-weight: 600;
		cursor: pointer;
	}

	.secondary {
		padding: 6px 10px;
		border-radius: 8px;
		border: 1px solid #cbd5f5;
		background: #ffffff;
		color: #1e293b;
		font-weight: 600;
		cursor: pointer;
	}

	.tag {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 4px 8px;
		border-radius: 999px;
		background: #e2e8f0;
		color: #475569;
		font-size: 12px;
	}

	.danger {
		align-self: flex-start;
		padding: 6px 10px;
		border-radius: 8px;
		border: 1px solid #fecaca;
		background: #fee2e2;
		color: #b91c1c;
		font-weight: 600;
		cursor: pointer;
	}

	.danger:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.form button:disabled,
	.form input:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.card ul {
		list-style: none;
		padding: 0;
		margin: 12px 0 0;
		display: grid;
		gap: 8px;
	}

	.card li {
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding: 10px 12px;
		border-radius: 12px;
		background: #f1f5f9;
		font-size: 13px;
	}

	.card li span {
		color: #64748b;
		word-break: break-all;
	}

	.link {
		color: #2563eb;
		font-weight: 600;
		text-decoration: none;
	}

	.link:hover {
		text-decoration: underline;
	}

	.preview {
		margin-top: 12px;
		padding: 12px;
		border-radius: 12px;
		background: #0f172a;
		color: #e2e8f0;
		font-size: 12px;
		max-height: 240px;
		overflow: auto;
	}

	.summary {
		margin-top: 12px;
		padding: 10px 12px;
		border-radius: 12px;
		background: #f8fafc;
		border: 1px solid #e2e8f0;
		display: grid;
		gap: 6px;
		font-size: 13px;
		color: #334155;
	}

	.muted {
		margin-top: 12px;
		color: #94a3b8;
		font-size: 13px;
	}

	.error {
		margin-top: 12px;
		color: #dc2626;
		font-size: 13px;
	}

	.success {
		margin-top: 12px;
		color: #16a34a;
		font-size: 13px;
	}
</style>
