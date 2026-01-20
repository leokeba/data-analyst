<script lang="ts">
	type AgentTool = {
		name: string;
		description: string;
		destructive: boolean;
	};

	type AgentRun = {
		id: string;
		status: string;
		plan: {
			steps: { id?: string; title: string; tool?: string | null }[];
			steps: { id?: string; title: string; tool?: string | null }[];
		log: Record<string, unknown>[];
		log: Record<string, unknown>[];
	};

	export let tools: AgentTool[] = [];
	export let toolsLoading = false;
	export let toolsError = "";
	export let runs: AgentRun[] = [];
	export let runsLoading = false;
	export let runsError = "";
	export let pageSize = 10;
	export let pageOffset = 0;
	export let hasNext = false;
	export let totalCount: number | null = null;
	export let onPrevPage: () => void;
	export let onNextPage: () => void;

	function stepStatus(run: AgentRun, stepId?: string) {
		if (!stepId) return "pending";
		const entry = run.log.find((item) => item.step_id === stepId);
		return (entry?.status as string) ?? "pending";
	}
	export let onRefresh: () => void;

	$: pageNumber = Math.floor(pageOffset / pageSize) + 1;
	$: rangeStart = runs.length ? pageOffset + 1 : 0;
	$: rangeEnd = totalCount !== null
		? Math.min(pageOffset + runs.length, totalCount)
		: pageOffset + runs.length;
</script>

<div class="card">
	<div class="card__header">
		<div>
			<h2>Agent runs</h2>
			<p>Track plan execution, tools, and outcomes.</p>
		</div>
		<button class="secondary" on:click={onRefresh}>Refresh</button>
	</div>

	<div class="summary">
		<strong>Available tools</strong>
		{#if toolsLoading}
			<span class="muted">Loading tools…</span>
		{:else if toolsError}
			<span class="error">{toolsError}</span>
		{:else if tools.length === 0}
			<span class="muted">No tools available.</span>
		{:else}
			<span>{tools.length} tools</span>
		{/if}
	</div>

	{#if tools.length}
		<ul>
			{#each tools as tool}
				<li>
					<strong>{tool.name}</strong>
					<span>{tool.description}</span>
					<span>Destructive: {tool.destructive ? "Yes" : "No"}</span>
				</li>
			{/each}
		</ul>
	{/if}

	<div class="summary">
		<strong>Run history</strong>
	</div>
	{#if runsLoading}
		<p class="muted">Loading agent runs…</p>
	{:else if runsError}
		<p class="error">{runsError}</p>
	{:else if runs.length === 0}
		<p class="muted">No agent runs yet.</p>
	{:else}
		<ul>
			{#each runs as run}
				<li>
					<strong>{run.plan.objective || "Untitled plan"}</strong>
					<span>Status: {run.status}</span>
					<span>Steps: {run.plan.steps.length}</span>
					<span>Run id: {run.id}</span>
					{#if run.plan.steps.length}
						<div class="summary">
							<strong>Steps</strong>
							<ul>
								{#each run.plan.steps as step}
									<li>
										<strong>{step.title}</strong>
										<span>Tool: {step.tool ?? "—"}</span>
										<span>Status: {stepStatus(run, step.id)}</span>
									</li>
								{/each}
							</ul>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	{#if !runsLoading && !runsError && runs.length && (hasNext || pageOffset > 0)}
		<div class="pager">
			<span class="pager__info">
				{#if totalCount !== null}
					Showing {rangeStart}–{rangeEnd} of {totalCount} · Page {pageNumber}
				{:else}
					Showing {rangeStart}–{rangeEnd} · Page {pageNumber}
				{/if}
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
