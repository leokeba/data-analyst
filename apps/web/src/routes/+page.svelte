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
	let selectedProjectId = "";
	let uploadFile: File | null = null;
	let uploadError = "";
	let uploadMessage = "";
	let isUploading = false;
	let datasets: Dataset[] = [];
	let datasetError = "";
	let datasetsLoading = false;
	let selectedDatasetId = "";
	let runType: RunType = "profile";
	let runMessage = "";
	let runError = "";
	let isRunning = false;

	const apiBase = "http://localhost:8000";

	type Dataset = {
		id: string;
		project_id: string;
		name: string;
		source: string;
		created_at: string;
	};

	type RunType = "ingest" | "profile" | "analysis" | "report";

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
			}
			await loadProjects();
		} catch (err) {
			createError = err instanceof Error ? err.message : "Failed to create project";
		} finally {
			isCreating = false;
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

	const handleProjectSelection = async (value: string) => {
		selectedProjectId = value;
		selectedDatasetId = "";
		if (value) {
			await loadDatasets(value);
		} else {
			datasets = [];
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

	const createRun = async () => {
		runMessage = "";
		runError = "";
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
			runMessage = "Run queued.";
		} catch (err) {
			runError = err instanceof Error ? err.message : "Run failed";
		} finally {
			isRunning = false;
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
						</li>
					{/each}
				</ul>
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
		</div>
		<div class="card">
			<h2>Reports</h2>
			<p>Publish markdown, HTML, and PDF outputs.</p>
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
