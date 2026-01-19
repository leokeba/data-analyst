<script lang="ts">
	type Dataset = {
		id: string;
		name: string;
	};

	type Run = {
		id: string;
		dataset_id: string;
		type: string;
		status: string;
		started_at: string;
		finished_at: string | null;
	};

	export let datasets: Dataset[] = [];
	export let selectedDatasetId = "";
	export let runType: "ingest" | "profile" | "analysis" | "report" = "profile";
	export let runTypeFilter: "all" | "ingest" | "profile" | "analysis" | "report" = "all";
	export let runSearch = "";
	export let runs: Run[] = [];
	export let filteredRuns: Run[] = [];
	export let runsLoading = false;
	export let runsError = "";
	export let runError = "";
	export let runMessage = "";
	export let runActionError = "";
	export let isRunning = false;
	export let deletingRunId = "";
	export let selectedProjectId = "";
	export let selectedRunId = "";
	export let onCreateRun: () => void;
	export let onSelectRun: (runId: string) => void;
	export let onDeleteRun: (runId: string) => void;
	export let datasetNameById: (datasetId: string) => string;
</script>

<div class="card">
	<div class="card__header">
		<div>
			<h2>Runs</h2>
			<p>Monitor profiling, analysis, and report runs.</p>
		</div>
	</div>
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
		<button on:click={onCreateRun} disabled={isRunning}>
			{isRunning ? "Queueing…" : "Queue run"}
		</button>
	</div>
	<div class="form">
		<select bind:value={runTypeFilter}>
			<option value="all">All types</option>
			<option value="ingest">ingest</option>
			<option value="profile">profile</option>
			<option value="analysis">analysis</option>
			<option value="report">report</option>
		</select>
		<input placeholder="Search runs" bind:value={runSearch} />
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
	{:else if filteredRuns.length === 0}
		<p class="muted">No runs match the filters.</p>
	{:else if filteredRuns.length > 0}
		<ul>
			{#each filteredRuns as run}
				<li>
					<strong>{run.type} · {run.status}</strong>
					<span>Dataset: {datasetNameById(run.dataset_id)}</span>
					<span>Started: {new Date(run.started_at).toLocaleString()}</span>
					{#if run.finished_at}
						<span>Finished: {new Date(run.finished_at).toLocaleString()}</span>
					{/if}
					<div class="card__actions">
						<button
							class="secondary"
							on:click={() => onSelectRun(run.id)}
							disabled={selectedRunId === run.id}
						>
							{selectedRunId === run.id ? "Selected" : "View"}
						</button>
						<button
							class="danger"
							on:click={() => onDeleteRun(run.id)}
							disabled={deletingRunId === run.id}
						>
							{deletingRunId === run.id ? "Deleting…" : "Delete"}
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>
