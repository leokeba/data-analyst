<script lang="ts">
	import { onMount } from 'svelte';
	import ProjectsCard from '$lib/components/ProjectsCard.svelte';
	import DatasetsCard from '$lib/components/DatasetsCard.svelte';
	import DatasetDetailCard from '$lib/components/DatasetDetailCard.svelte';
	import RunsCard from '$lib/components/RunsCard.svelte';
	import ReportsCard from '$lib/components/ReportsCard.svelte';

	const apiBase = 'http://localhost:8000';

	// Helper types
	type Project = { id: string; name: string; workspace_path: string };
	type Dataset = {
		id: string;
		name: string;
		source: string;
		stats?: {
			row_count?: number;
			column_count?: number;
			file_size_bytes?: number;
			duplicate_row_count?: number;
			missing_by_column?: Record<string, number>;
		};
		schema_snapshot?: {
			columns?: { name: string; index: number }[];
		};
	};
	type Run = {
		id: string;
		dataset_id: string;
		type: string;
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

	// State - Projects
	let projects: Project[] = [];
	let loading = true;
	let error = '';
	let createError = '';
	let projectActionError = '';
	let newProjectName = '';
	let isCreating = false;
	let deletingProjectId = '';
	let selectedProjectId = '';

	// State - Datasets
	let datasets: Dataset[] = [];
	let datasetsLoading = false;
	let datasetError = '';
	let uploadError = '';
	let uploadMessage = '';
	let isUploading = false;
	let uploaderInput: File | null = null;
	let newDatasetName = '';
	let newDatasetSource = '';
	let isCreatingDataset = false;
	let createDatasetError = '';
	let datasetActionError = '';
	let deletingDatasetId = '';
	let selectedDatasetId = '';
	let selectedDataset: Dataset | null = null;
	let datasetPreviewLoading = false;
	let datasetPreviewError = '';
	let datasetPreviewData: { columns: string[]; rows: string[][] } | null = null;

	// State - Runs
	let runs: Run[] = [];
	let runsLoading = false;
	let runsError = '';
	let runError = '';
	let runMessage = '';
	let runActionError = '';
	let isRunning = false;
	let deletingRunId = '';
	let selectedRunId = '';
	let selectedRun: Run | null = null;
	let runType: 'ingest' | 'profile' | 'analysis' | 'report' = 'profile';
	let runTypeFilter: 'all' | 'ingest' | 'profile' | 'analysis' | 'report' = 'all';
	let runSearch = '';

	// State - Artifacts/Reports
	let selectedRunArtifacts: Artifact[] = [];
	let selectedRunArtifactTypes: string[] = [];
	let artifactsLoading = false;
	let artifactsError = '';
	let artifactActionError = '';
	let artifactTypeFilter = 'all';
	let artifactSearch = '';
	let previewError = ''; 
	
	// Artifact Preview State
	let previewContent = '';
	let previewLoading = false;
	let previewArtifactId = '';
	let deletingArtifactId = '';

	// Derived
	$: if (selectedDatasetId && datasets.length) {
		selectedDataset = datasets.find((d) => d.id === selectedDatasetId) || null;
	} else {
		selectedDataset = null;
	}

	$: if (selectedRunId && runs.length) {
		selectedRun = runs.find((r) => r.id === selectedRunId) || null;
	} else {
		selectedRun = null;
	}
	
	$: latestRunForDataset = (datasetId: string) => {
		const datasetRuns = runs.filter(r => r.dataset_id === datasetId);
		if (datasetRuns.length === 0) return null;
		return datasetRuns.sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())[0];
	};

	$: datasetNameById = (datasetId: string) => {
		const ds = datasets.find(d => d.id === datasetId);
		return ds ? ds.name : 'Unknown';
	};
	
	$: filteredRuns = runs.filter((r) => {
		if (runTypeFilter !== 'all' && r.type !== runTypeFilter) return false;
		if (runSearch) {
			const dsName = datasetNameById(r.dataset_id).toLowerCase();
			return dsName.includes(runSearch.toLowerCase()) || r.id.includes(runSearch);
		}
		return true;
	});

	$: filteredArtifacts = selectedRunArtifacts.filter((a) => {
		if (artifactTypeFilter !== 'all' && a.type !== artifactTypeFilter) return false;
		if (artifactSearch && !a.path.toLowerCase().includes(artifactSearch.toLowerCase()))
			return false;
		return true;
	});
	
	$: artifactTypes = Array.from(new Set(selectedRunArtifacts.map(a => a.type)));

	// Initialization
	onMount(() => {
		loadProjects();
	});

	// --- API Actions ---

	async function loadProjects() {
		loading = true;
		error = '';
		try {
			const res = await fetch(`${apiBase}/projects`);
			if (!res.ok) throw new Error('Failed to fetch projects');
			projects = await res.json();
		} catch (e) {
			error = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	async function createProject() {
		if (!newProjectName) return;
		isCreating = true;
		createError = '';
		try {
			const res = await fetch(`${apiBase}/projects`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ name: newProjectName })
			});
			if (!res.ok) throw new Error('Failed to create project');
			const project = await res.json();
			projects = [...projects, project];
			newProjectName = '';
			selectProject(project.id);
		} catch (e) {
			createError = (e as Error).message;
		} finally {
			isCreating = false;
		}
	}

	async function deleteProject(projectId: string) {
		if (!confirm('Are you sure you want to delete this project?')) return;
		deletingProjectId = projectId;
		projectActionError = '';
		try {
			const res = await fetch(`${apiBase}/projects/${projectId}`, { method: 'DELETE' });
			if (!res.ok) throw new Error('Failed to delete project');
			projects = projects.filter((p) => p.id !== projectId);
			if (selectedProjectId === projectId) {
				selectedProjectId = '';
				datasets = [];
				runs = [];
			}
		} catch (e) {
			projectActionError = (e as Error).message;
		} finally {
			deletingProjectId = '';
		}
	}

	async function selectProject(projectId: string) {
		selectedProjectId = projectId;
		datasets = [];
		runs = [];
		selectedDatasetId = '';
		selectedRunId = '';
		selectedRunArtifacts = [];
		await Promise.all([loadDatasets(), loadRuns()]);
	}

	// Datasets
	async function loadDatasets() {
		if (!selectedProjectId) return;
		datasetsLoading = true;
		datasetError = '';
		try {
			const res = await fetch(`${apiBase}/projects/${selectedProjectId}/datasets`);
			if (!res.ok) throw new Error('Failed to fetch datasets');
			datasets = await res.json();
		} catch (e) {
			datasetError = (e as Error).message;
		} finally {
			datasetsLoading = false;
		}
	}

	async function ingestFile() {
		if (!uploaderInput || !selectedProjectId) return;
		isUploading = true;
		uploadError = '';
		uploadMessage = '';
		try {
			const formData = new FormData();
			formData.append('file', uploaderInput);
			const res = await fetch(
				`${apiBase}/projects/${selectedProjectId}/datasets/upload`,
				{
					method: 'POST',
					body: formData
				}
			);
			if (!res.ok) throw new Error('Upload failed');
			const dataset = await res.json();
			uploadMessage = 'Upload successful';
			datasets = [...datasets, dataset];
		} catch (e) {
			uploadError = (e as Error).message;
		} finally {
			isUploading = false;
			uploaderInput = null;
		}
	}

	async function createDataset() {
		if (!newDatasetName || !newDatasetSource || !selectedProjectId) return;
		isCreatingDataset = true;
		createDatasetError = '';
		try {
			const res = await fetch(`${apiBase}/projects/${selectedProjectId}/datasets`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ name: newDatasetName, source: newDatasetSource })
			});
			if (!res.ok) throw new Error('Failed to create dataset');
			const dataset = await res.json();
			datasets = [...datasets, dataset];
			newDatasetName = '';
			newDatasetSource = '';
		} catch (e) {
			createDatasetError = (e as Error).message;
		} finally {
			isCreatingDataset = false;
		}
	}

	async function deleteDataset(datasetId: string) {
		if (!confirm('Delete dataset?')) return;
		deletingDatasetId = datasetId;
		datasetActionError = '';
		try {
			const res = await fetch(`${apiBase}/projects/${selectedProjectId}/datasets/${datasetId}`, {
				method: 'DELETE'
			});
			if (!res.ok) throw new Error('Failed to delete dataset');
			datasets = datasets.filter((d) => d.id !== datasetId);
			if (selectedDatasetId === datasetId) selectedDatasetId = '';
		} catch (e) {
			datasetActionError = (e as Error).message;
		} finally {
			deletingDatasetId = '';
		}
	}

	function selectDataset(datasetId: string) {
		selectedDatasetId = datasetId;
	}

	async function previewDataset() {
		if (!selectedDatasetId || !selectedProjectId) return;
		datasetPreviewLoading = true;
		datasetPreviewError = '';
		datasetPreviewData = null;
		try {
			const datasetSource = selectedDataset?.source ?? '';
			const sourceLower = datasetSource.toLowerCase();
			if (datasetSource && !sourceLower.endsWith('.csv')) {
				datasetPreviewError = 'Preview is only available for CSV datasets.';
				return;
			}
			const res = await fetch(
				`${apiBase}/projects/${selectedProjectId}/datasets/${selectedDatasetId}/preview`
			);
			if (!res.ok) throw new Error('Preview not available');
			const payload = await res.json();
			datasetPreviewData = payload;
		} catch (e) {
			datasetPreviewError = (e as Error).message;
		} finally {
			datasetPreviewLoading = false;
		}
	}

	// Runs
	async function loadRuns() {
		if (!selectedProjectId) return;
		runsLoading = true;
		runsError = '';
		try {
			const res = await fetch(`${apiBase}/projects/${selectedProjectId}/runs`);
			if (!res.ok) throw new Error('Failed to fetch runs');
			runs = await res.json();
		} catch (e) {
			runsError = (e as Error).message;
		} finally {
			runsLoading = false;
		}
	}

	async function createRun() {
		if (!selectedDatasetId || !selectedProjectId) return;
		isRunning = true;
		runError = '';
		try {
			const res = await fetch(`${apiBase}/projects/${selectedProjectId}/runs`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ dataset_id: selectedDatasetId, type: runType })
			});
			if (!res.ok) throw new Error('Failed to start run');
			const run = await res.json();
			runs = [run, ...runs];
			runMessage = `Started ${runType} run`;
			pollRun(run.id);
		} catch (e) {
			runError = (e as Error).message;
		} finally {
			isRunning = false;
		}
	}

	async function pollRun(runId: string) {
		const interval = setInterval(async () => {
			try {
				const res = await fetch(
					`${apiBase}/projects/${selectedProjectId}/runs/${runId}`
				);
				if (res.ok) {
					const updated = await res.json();
					runs = runs.map((r) => (r.id === runId ? updated : r));
					if (updated.status === 'completed' || updated.status === 'failed') {
						clearInterval(interval);
						if (updated.status === 'completed') {
							// Refresh artifacts if this run is selected
							if (selectedRunId === runId) loadArtifacts(runId);
						}
					}
				}
			} catch {
				clearInterval(interval);
			}
		}, 1000);
	}

	async function deleteRun(runId: string) {
		if (!confirm('Delete run?')) return;
		deletingRunId = runId;
		runActionError = '';
		try {
			const res = await fetch(`${apiBase}/projects/${selectedProjectId}/runs/${runId}`, {
				method: 'DELETE'
			});
			if (!res.ok) throw new Error('Failed to delete run');
			runs = runs.filter((r) => r.id !== runId);
			if (selectedRunId === runId) selectedRunId = '';
		} catch (e) {
			runActionError = (e as Error).message;
		} finally {
			deletingRunId = '';
		}
	}

	function selectRun(runId: string) {
		selectedRunId = runId;
		loadArtifacts(runId);
	}

	// Artifacts
	async function loadArtifacts(runId: string) {
		if (!selectedProjectId) return;
		artifactsLoading = true;
		artifactsError = '';
		selectedRunArtifacts = [];
		try {
			const res = await fetch(`${apiBase}/projects/${selectedProjectId}/artifacts`);
			if (!res.ok) throw new Error('Failed to fetch artifacts');
			const all = await res.json();
			selectedRunArtifacts = all.filter((a: Artifact) => a.run_id === runId);
		} catch (e) {
			artifactsError = (e as Error).message;
		} finally {
			artifactsLoading = false;
		}
	}

	async function onPreviewArtifact(artifactId: string) {
		previewArtifactId = artifactId;
		previewLoading = true;
		try {
			const res = await fetch(`${apiBase}/projects/${selectedProjectId}/artifacts/${artifactId}/download`);
			if(res.ok) {
				const text = await res.text();
				previewContent = text;
			}
		} catch(e) {
			previewError = (e as Error).message;
		} finally {
			previewLoading = false;
		}
	}

	async function deleteArtifact(artifactId: string) {
		if(!confirm("Delete this artifact?")) return;
		deletingArtifactId = artifactId;
		try {
			const res = await fetch(`${apiBase}/projects/${selectedProjectId}/artifacts/${artifactId}`, { method: 'DELETE' });
			if(!res.ok) throw new Error("Delete failed");
			selectedRunArtifacts = selectedRunArtifacts.filter(a => a.id !== artifactId);
		} catch(e) {
			artifactActionError = (e as Error).message;
		} finally {
			deletingArtifactId = '';
		}
	}

	function clearRunSelection() {
		selectedRunId = '';
		selectedRunArtifacts = [];
	}

	async function rerunSelected() {
		if (!selectedRun) return;
		runType = selectedRun.type as any;
		selectedDatasetId = selectedRun.dataset_id;
		await createRun();
	}
</script>

<div class="layout">
	<div class="sidebar">
		<ProjectsCard
			{projects}
			{loading}
			{error}
			{createError}
			{projectActionError}
			bind:newProjectName
			{isCreating}
			{deletingProjectId}
			{selectedProjectId}
			onCreate={createProject}
			onDelete={deleteProject}
			onSelect={selectProject}
		/>
	</div>
	<div class="main">
		{#if selectedProjectId}
			<DatasetsCard
				{projects}
				bind:selectedProjectId
				{datasets}
				{datasetsLoading}
				{datasetError}
				{uploadError}
				{uploadMessage}
				{isUploading}
				bind:newDatasetName
				bind:newDatasetSource
				{isCreatingDataset}
				{createDatasetError}
				{datasetActionError}
				{deletingDatasetId}
				bind:selectedDatasetId
				onProjectChange={(id) => selectProject(id)}
				onUpload={ingestFile}
				onCreateDataset={createDataset}
				onDeleteDataset={deleteDataset}
				onSelectDataset={selectDataset}
				onFileChange={(file) => { uploaderInput = file; }}
				{latestRunForDataset}
			/>

			{#if selectedDatasetId}
				<DatasetDetailCard
					{selectedProjectId}
					{selectedDatasetId}
					dataset={selectedDataset}
					{datasetPreviewLoading}
					{datasetPreviewError}
					datasetPreviewData={datasetPreviewData}
					onPreview={previewDataset}
					{apiBase}
				/>
			{/if}

			<RunsCard
				{datasets}
				bind:selectedDatasetId
				bind:runType
				bind:runTypeFilter
				bind:runSearch
				runs={runs}
				{filteredRuns}
				{runsLoading}
				{runsError}
				{runError}
				{runMessage}
				{runActionError}
				{isRunning}
				{deletingRunId}
				{selectedProjectId}
				{selectedRunId}
				onCreateRun={createRun}
				onSelectRun={selectRun}
				onDeleteRun={deleteRun}
				{datasetNameById}
			/>

			{#if selectedRunId}
				<ReportsCard
					{selectedProjectId}
					{selectedRunId}
					run={selectedRun}
					{selectedRunArtifacts}
					{selectedRunArtifactTypes}
					{artifactsLoading}
					{artifactsError}
					{artifactActionError}
					{filteredArtifacts}
					{artifactTypes}
					bind:artifactTypeFilter
					bind:artifactSearch
					{previewError}
					{previewContent}
					{previewLoading}
					{previewArtifactId}
					{deletingArtifactId}
					{apiBase}
					onPreviewArtifact={onPreviewArtifact}
					onDeleteArtifact={deleteArtifact}
					onClearRunFilter={clearRunSelection}
					onRerunSelected={rerunSelected}
				/>
			{/if}
		{:else}
			<div class="welcome">
				<h2>Welcome to Data Analyst</h2>
				<p>Select or create a project to get started.</p>
			</div>
		{/if}
	</div>
</div>

<style>
	:global(body) {
		font-family: system-ui, -apple-system, sans-serif;
		margin: 0;
		padding: 20px;
		background: #f4f4f5;
		color: #18181b;
	}
	.layout {
		display: grid;
		grid-template-columns: 320px 1fr;
		gap: 24px;
		max-width: 1400px;
		margin: 0 auto;
	}
	.sidebar {
		display: flex;
		flex-direction: column;
		gap: 24px;
	}
	.main {
		display: flex;
		flex-direction: column;
		gap: 24px;
	}
	.welcome {
		text-align: center;
		padding: 60px;
		background: white;
		border-radius: 8px;
		border: 1px dashed #e4e4e7;
		color: #71717a;
	}
	/* Shared Component Styles */
	:global(.card) { 
		background: white; 
		border: 1px solid #e4e4e7; 
		border-radius: 8px; 
		padding: 24px; 
		display: flex; 
		flex-direction: column; 
		gap: 16px; 
		box-shadow: 0 1px 2px rgba(0,0,0,0.05); 
	}
	:global(.card__header h2) { margin: 0; font-size: 18px; font-weight: 600; }
	:global(.card__header p) { margin: 4px 0 0; color: #71717a; font-size: 14px; }
	:global(.form) { display: flex; gap: 8px; flex-wrap: wrap; }
	:global(.form input), :global(.form select) { flex: 1; padding: 8px 12px; border: 1px solid #e4e4e7; border-radius: 6px; font-size: 14px; min-width: 200px; }
	:global(.form button) { padding: 8px 16px; border-radius: 6px; font-weight: 500; cursor: pointer; background: #18181b; color: white; border: none; flex-shrink: 0; }
	:global(.form button:disabled) { opacity: 0.5; cursor: not-allowed; }
	:global(.error) { color: #ef4444; font-size: 14px; margin: 0; }
	:global(.muted) { color: #71717a; font-size: 14px; margin: 0; }
	:global(.card ul) { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
	:global(.card ul li) { padding: 12px; border: 1px solid #f4f4f5; border-radius: 8px; display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; }
	:global(.card ul li:last-child) { border-bottom: none; }
	:global(.card ul li strong) { display: block; font-size: 14px; }
	:global(.card ul li span) { display: block; font-size: 12px; color: #71717a; margin-top: 2px; word-break: break-all; overflow-wrap: anywhere; }
	:global(.card ul li .actions) { margin-left: auto; flex-shrink: 0; }
	:global(button.danger) { background: #fee2e2; color: #ef4444; }
	:global(button.danger:hover) { background: #fecaca; }
	:global(.link) { color: #2563eb; text-decoration: none; font-size: 14px; }
	:global(.link:hover) { text-decoration: underline; }
	:global(.actions) { display: flex; gap: 8px; align-items: center; }
	:global(li.selected) { background: #f4f4f5; border-radius: 6px; }
	:global(.summary) { font-size: 14px; display: flex; flex-direction: column; gap: 4px; }
	:global(.tag) { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 12px; font-weight: 500; background: #f4f4f5; color: #52525b; text-transform: uppercase; }
	:global(.tag.success) { background: #dcfce7; color: #15803d; }
	:global(.tag.failure) { background: #fee2e2; color: #b91c1c; }
	:global(.tag.running) { background: #e0f2fe; color: #0369a1; }
	:global(.preview) { overflow-x: auto; border: 1px solid #e4e4e7; border-radius: 8px; padding: 12px; background: #fafafa; }
	:global(.preview table) { width: 100%; border-collapse: collapse; font-size: 12px; }
	:global(.preview th, .preview td) { text-align: left; padding: 6px 8px; border-bottom: 1px solid #e4e4e7; white-space: nowrap; }
	:global(.preview th) { background: #f4f4f5; font-weight: 600; position: sticky; top: 0; }
</style>
