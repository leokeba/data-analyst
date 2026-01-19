<script lang="ts">
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

	export let selectedProjectId = "";
	export let selectedDatasetId = "";
	export let dataset: Dataset | null = null;
	export let datasetPreviewLoading = false;
	export let datasetPreviewError = "";
	export let datasetPreviewContent = "";
	export let onPreview: () => void;
	export let apiBase = "";
</script>

<div class="card">
	<div class="card__header">
		<div>
			<h2>Dataset detail</h2>
			<p>Review schema and basic stats.</p>
		</div>
	</div>
	{#if !selectedDatasetId}
		<p class="muted">Select a dataset to see details.</p>
	{:else if !dataset}
		<p class="muted">Dataset not found.</p>
	{:else}
		<div class="summary">
			<strong>{dataset.name}</strong>
			<span>Source: {dataset.source}</span>
			{#if selectedProjectId}
				<a
					class="link"
					href={`${apiBase}/projects/${selectedProjectId}/datasets/${selectedDatasetId}/download`}
					target="_blank"
					rel="noreferrer"
				>
					Download dataset
				</a>
			{/if}
			{#if dataset.stats}
				<span>Rows: {dataset.stats.row_count ?? "—"}</span>
				<span>Columns: {dataset.stats.column_count ?? "—"}</span>
				<span>File size: {dataset.stats.file_size_bytes ?? "—"} bytes</span>
				<span>Duplicate rows: {dataset.stats.duplicate_row_count ?? "—"}</span>
			{/if}
			<button class="secondary" on:click={onPreview} disabled={datasetPreviewLoading}>
				{datasetPreviewLoading ? "Loading…" : "Preview"}
			</button>
		</div>
		{#if dataset.stats?.missing_by_column}
			<ul>
				{#each Object.entries(dataset.stats?.missing_by_column ?? {}) as [name, count]}
					<li>
						<strong>{name}</strong>
						<span>Missing: {count}</span>
					</li>
				{/each}
			</ul>
		{/if}
		{#if dataset.schema_snapshot?.columns?.length}
			<ul>
				{#each dataset.schema_snapshot?.columns ?? [] as column}
					<li>
						<strong>{column.name}</strong>
						<span>Index: {column.index}</span>
					</li>
				{/each}
			</ul>
		{/if}
	{/if}
	{#if datasetPreviewError}
		<p class="error">{datasetPreviewError}</p>
	{/if}
	{#if datasetPreviewContent}
		<pre class="preview">{datasetPreviewContent}</pre>
	{/if}
</div>
