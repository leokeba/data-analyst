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
			objective: string;
			steps: {
				id?: string;
				title: string;
				tool?: string | null;
				requires_approval?: boolean;
			}[];
		};
		log: {
			step_id?: string;
			status?: string;
			approvals?: { approved_by?: string; approved_at?: string; note?: string | null }[];
		}[];
	};

	type AgentSnapshot = {
		id: string;
		kind: string;
		target_path: string;
		created_at: string;
		run_id?: string | null;
		details?: Record<string, unknown> | null;
	};

	export let tools: AgentTool[] = [];
	export let toolsLoading = false;
	export let toolsError = "";
	export let runs: AgentRun[] = [];
	export let runsLoading = false;
	export let runsError = "";
	export let actionError = "";
	export let snapshots: AgentSnapshot[] = [];
	export let snapshotsLoading = false;
	export let snapshotsError = "";
	export let rollbackError = "";
	export let snapshotsPageSize = 10;
	export let snapshotsPageOffset = 0;
	export let snapshotsHasNext = false;
	export let snapshotsTotal: number | null = null;
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

	function stepLog(run: AgentRun, stepId?: string) {
		if (!stepId) return undefined;
		return run.log.find((item) => item.step_id === stepId);
	}

	function completionPercent(run: AgentRun) {
		if (!run.plan.steps.length) return 0;
		const applied = run.plan.steps.filter(
			(step) => stepStatus(run, step.id) === "applied"
		).length;
		return Math.round((applied / run.plan.steps.length) * 100);
	}
	export let onRefresh: () => void;
	export let onReplayRun: (run: AgentRun) => void;
	export let onPrevSnapshotsPage: () => void;
	export let onNextSnapshotsPage: () => void;
	export let onRequestRollback: (snapshot: AgentSnapshot) => void;

	$: pageNumber = Math.floor(pageOffset / pageSize) + 1;
	$: rangeStart = runs.length ? pageOffset + 1 : 0;
	$: rangeEnd = totalCount !== null
		? Math.min(pageOffset + runs.length, totalCount)
		: pageOffset + runs.length;
	$: snapshotsPageNumber = Math.floor(snapshotsPageOffset / snapshotsPageSize) + 1;
	$: snapshotsRangeStart = snapshots.length ? snapshotsPageOffset + 1 : 0;
	$: snapshotsRangeEnd = snapshotsTotal !== null
		? Math.min(snapshotsPageOffset + snapshots.length, snapshotsTotal)
		: snapshotsPageOffset + snapshots.length;
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
					<span>Completion: {completionPercent(run)}%</span>
					<span>Run id: {run.id}</span>
					<div class="card__actions">
						<button class="secondary" on:click={() => onReplayRun(run)}>
							Replay
						</button>
					</div>
					{#if run.plan.steps.length}
						<div class="summary">
							<strong>Steps</strong>
							<ul>
								{#each run.plan.steps as step}
									{@const entry = stepLog(run, step.id)}
									<li>
										<strong>{step.title}</strong>
										<span>Tool: {step.tool ?? "—"}</span>
										{#if step.requires_approval}
											<span>Approval required</span>
										{/if}
										<span>Status: {stepStatus(run, step.id)}</span>
										{#if entry?.approvals?.length}
											<span>
												Approvals: {entry.approvals.length}
											</span>
										{/if}
									</li>
								{/each}
							</ul>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
	{#if actionError}
		<p class="error">{actionError}</p>
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

	<div class="summary">
		<strong>Snapshots</strong>
	</div>
	{#if snapshotsLoading}
		<p class="muted">Loading snapshots…</p>
	{:else if snapshotsError}
		<p class="error">{snapshotsError}</p>
	{:else if snapshots.length === 0}
		<p class="muted">No snapshots yet.</p>
	{:else}
		<ul>
			{#each snapshots as snapshot}
				<li>
					<strong>{snapshot.kind}</strong>
					<span>Path: {snapshot.target_path}</span>
					<span>Created: {new Date(snapshot.created_at).toLocaleString()}</span>
					{#if snapshot.run_id}
						<span>Run: {snapshot.run_id}</span>
					{/if}
					<div class="card__actions">
						<button class="secondary" on:click={() => onRequestRollback(snapshot)}>
							Request rollback
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
	{#if rollbackError}
		<p class="error">{rollbackError}</p>
	{/if}

	{#if !snapshotsLoading && !snapshotsError && snapshots.length && (snapshotsHasNext || snapshotsPageOffset > 0)}
		<div class="pager">
			<span class="pager__info">
				{#if snapshotsTotal !== null}
					Showing {snapshotsRangeStart}–{snapshotsRangeEnd} of {snapshotsTotal} · Page {snapshotsPageNumber}
				{:else}
					Showing {snapshotsRangeStart}–{snapshotsRangeEnd} · Page {snapshotsPageNumber}
				{/if}
			</span>
			<div class="pager__actions">
				<button class="secondary" on:click={onPrevSnapshotsPage} disabled={snapshotsPageOffset === 0}>
					Previous
				</button>
				<button class="secondary" on:click={onNextSnapshotsPage} disabled={!snapshotsHasNext}>
					Next
				</button>
			</div>
		</div>
	{/if}
</div>
