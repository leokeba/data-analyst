<script lang="ts">
	type Run = {
		id: string;
		type: string;
		status: string;
		dataset_id: string;
	};

	type Artifact = {
		id: string;
		run_id: string;
		type: string;
		path: string;
		mime_type: string;
		size: number;
	};

	export let selectedProjectId = "";
	export let selectedRunId = "";
	export let run: Run | null = null;
	export let selectedRunArtifacts: Artifact[] = [];
	export let selectedRunArtifactTypes: string[] = [];
	export let artifactsLoading = false;
	export let artifactsError = "";
	export let artifactActionError = "";
	export let filteredArtifacts: Artifact[] = [];
	export let artifactTypes: string[] = [];
	export let artifactTypeFilter = "all";
	export let artifactSearch = "";
	export let previewError = "";
	export let previewContent = "";
	export let previewLoading = false;
	export let previewArtifactId = "";
	export let deletingArtifactId = "";
	export let apiBase = "";
	export let onClearRunFilter: () => void;
	export let onPreviewArtifact: (artifactId: string) => void;
	export let onDeleteArtifact: (artifactId: string) => void;
	export let onRerunSelected: () => void;
</script>

<div class="card">
	<div class="card__header">
		<div>
			<h2>Reports</h2>
			<p>Publish markdown, HTML, and PDF outputs.</p>
		</div>
	</div>
	<div class="form">
		{#if selectedRunId}
			<span class="tag">Filtered by run: {selectedRunId}</span>
			<button class="secondary" on:click={onClearRunFilter}>Show all</button>
		{/if}
	</div>
	{#if selectedRunId && run}
		<div class="summary">
			<strong>Run details</strong>
			<span>Type: {run.type}</span>
			<span>Status: {run.status}</span>
			{#if selectedRunArtifacts.length}
				<span>Artifacts: {selectedRunArtifacts.length}</span>
				<span>Types: {selectedRunArtifactTypes.join(", ")}</span>
			{/if}
			<button class="secondary" on:click={onRerunSelected}>Re-run</button>
		</div>
	{/if}
	<div class="form">
		<select bind:value={artifactTypeFilter}>
			<option value="all">All types</option>
			{#each artifactTypes as type}
				<option value={type}>{type}</option>
			{/each}
		</select>
		<input placeholder="Search artifacts" bind:value={artifactSearch} />
	</div>
	{#if artifactsLoading}
		<p class="muted">Loading artifacts…</p>
	{:else if artifactsError}
		<p class="error">{artifactsError}</p>
	{:else if selectedProjectId && filteredArtifacts.length === 0}
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
						<div class="card__actions">
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
								on:click={() => onPreviewArtifact(artifact.id)}
								disabled={previewLoading && previewArtifactId === artifact.id}
							>
								{previewLoading && previewArtifactId === artifact.id
									? "Loading…"
									: "Preview"}
							</button>
							<button
								class="danger"
								on:click={() => onDeleteArtifact(artifact.id)}
								disabled={deletingArtifactId === artifact.id}
							>
								{deletingArtifactId === artifact.id ? "Deleting…" : "Delete"}
							</button>
						</div>
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
	{#if artifactActionError}
		<p class="error">{artifactActionError}</p>
	{/if}
</div>
