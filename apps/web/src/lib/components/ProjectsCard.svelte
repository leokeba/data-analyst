<script lang="ts">
	type Project = {
		id: string;
		name: string;
		workspace_path: string;
	};

	export let projects: Project[] = [];
	export let loading = false;
	export let error = "";
	export let createError = "";
	export let projectActionError = "";
	export let newProjectName = "";
	export let isCreating = false;
	export let deletingProjectId = "";
	export let selectedProjectId = "";
	export let pageSize = 10;
	export let pageOffset = 0;
	export let hasNext = false;
	export let onCreate: () => void;
	export let onDelete: (projectId: string) => void;
	export let onSelect: (projectId: string) => void;
	export let onPrevPage: () => void;
	export let onNextPage: () => void;

	$: pageNumber = Math.floor(pageOffset / pageSize) + 1;
	$: rangeStart = projects.length ? pageOffset + 1 : 0;
	$: rangeEnd = pageOffset + projects.length;
</script>

<div class="card">
	<div class="card__header">
		<div>
			<h2>Projects</h2>
			<p>Create isolated workspaces and environments.</p>
		</div>
	</div>
	<div class="form">
		<input
			placeholder="Project name"
			bind:value={newProjectName}
			disabled={isCreating}
		/>
		<button on:click={onCreate} disabled={isCreating}>
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
				<li class:selected={selectedProjectId === project.id}>
					<strong>{project.name}</strong>
					<span>{project.workspace_path}</span>
					<div class="actions">
						<button on:click={() => onSelect(project.id)}>
							{selectedProjectId === project.id ? "Active" : "Select"}
						</button>
						<button
							class="danger"
							on:click={() => onDelete(project.id)}
							disabled={deletingProjectId === project.id}
						>
							{deletingProjectId === project.id ? "Deleting…" : "Delete"}
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
	{#if projectActionError}
		<p class="error">{projectActionError}</p>
	{/if}
	{#if !loading && !error && projects.length && (hasNext || pageOffset > 0)}
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
