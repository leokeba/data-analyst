<script lang="ts">
	type Project = {
		id: string;
		name: string;
	};

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
		type: string;
		status: string;
		dataset_id: string;
	};

	export let projects: Project[] = [];
	export let selectedProjectId = "";
	export let datasets: Dataset[] = [];
	export let datasetsLoading = false;
	export let datasetError = "";
	export let uploadError = "";
	export let uploadMessage = "";
	export let isUploading = false;
	export let newDatasetName = "";
	export let newDatasetSource = "";
	export let isCreatingDataset = false;
	export let createDatasetError = "";
	export let datasetActionError = "";
	export let deletingDatasetId = "";
	export let selectedDatasetId = "";
	export let pageSize = 10;
	export let pageOffset = 0;
	export let hasNext = false;
	export let onProjectChange: (value: string) => void;
	export let onUpload: () => void;
	export let onCreateDataset: () => void;
	export let onDeleteDataset: (datasetId: string) => void;
	export let onSelectDataset: (datasetId: string) => void;
	export let onFileChange: (file: File | null) => void;
	export let latestRunForDataset: (datasetId: string) => Run | null;
	export let onPrevPage: () => void;
	export let onNextPage: () => void;

	$: pageNumber = Math.floor(pageOffset / pageSize) + 1;
	$: rangeStart = datasets.length ? pageOffset + 1 : 0;
	$: rangeEnd = pageOffset + datasets.length;
</script>

<div class="card">
	<div class="card__header">
		<div>
			<h2>Datasets</h2>
			<p>Track ingestion sources, profiling, and quality checks.</p>
		</div>
	</div>
	<div class="form">
		<select
			bind:value={selectedProjectId}
			disabled={isUploading}
			on:change={(event) => onProjectChange((event.target as HTMLSelectElement).value)}
		>
			<option value="">Select project</option>
			{#each projects as project}
				<option value={project.id}>{project.name}</option>
			{/each}
		</select>
		<input
			type="file"
			on:change={(event) => {
				const target = event.currentTarget as HTMLInputElement;
				onFileChange(target.files ? target.files[0] : null);
			}}
			disabled={isUploading}
		/>
		<button on:click={onUpload} disabled={isUploading}>
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
		<button on:click={onCreateDataset} disabled={isCreatingDataset}>
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
					{#if latestRunForDataset(dataset.id)}
						<span>
							Last run: {latestRunForDataset(dataset.id)?.type} · {latestRunForDataset(dataset.id)?.status}
						</span>
					{/if}
					<div class="card__actions">
						<button
							class="secondary"
							on:click={() => onSelectDataset(dataset.id)}
							disabled={selectedDatasetId === dataset.id}
						>
							{selectedDatasetId === dataset.id ? "Selected" : "View"}
						</button>
						<button
							class="danger"
							on:click={() => onDeleteDataset(dataset.id)}
							disabled={deletingDatasetId === dataset.id}
						>
							{deletingDatasetId === dataset.id ? "Deleting…" : "Delete"}
						</button>
					</div>
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
	{#if !datasetsLoading && !datasetError && datasets.length && (hasNext || pageOffset > 0)}
		<div class="pager">
			<span class="pager__info">
				Showing {rangeStart}–{rangeEnd} · Page {pageNumber}
			</span>
			<div class="pager__actions">
				<button class="secondary" on:click={onPrevPage} disabled={pageOffset === 0}>
					Previous
				</button>
				<button class="secondary" on:click={onNextPage} disabled={!hasNext}>
					Next
				</button>
			</div>
		</div>
	{/if}
</div>
