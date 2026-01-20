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
			tool?: string | null;
			artifacts?: string[];
			diff?: string | null;
			output?: Record<string, unknown> | null;
			approvals?: { approved_by?: string; approved_at?: string; note?: string | null }[];
		}[];
	};

	type AgentSnapshot = {
		id: string;
		project_id: string;
		kind: string;
		target_path: string;
		created_at: string;
		run_id?: string | null;
		details?: Record<string, unknown> | null;
	};

	type AgentRollback = {
		id: string;
		project_id: string;
		status: string;
		created_at: string;
		run_id?: string | null;
		snapshot_id?: string | null;
		note?: string | null;
	};

	type AgentArtifact = {
		id: string;
		run_id?: string | null;
		snapshot_id?: string | null;
		type: string;
		path: string;
		mime_type: string;
		size: number;
		created_at: string;
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
	export let rollbacks: AgentRollback[] = [];
	export let rollbacksLoading = false;
	export let rollbacksError = "";
	export let rollbacksPageSize = 10;
	export let rollbacksPageOffset = 0;
	export let rollbacksHasNext = false;
	export let rollbacksTotal: number | null = null;
	export let artifacts: AgentArtifact[] = [];
	export let artifactsLoading = false;
	export let artifactsError = "";
	export let previewArtifactId = "";
	export let previewLoading = false;
	export let previewContent = "";
	export let previewMimeType = "";
	export let apiBase = "";
	export let projectId = "";
	export let safeMode = true;
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
	export let onRestoreSnapshot: (snapshot: AgentSnapshot) => void;
	export let onPrevRollbacksPage: () => void;
	export let onNextRollbacksPage: () => void;
	export let onApplyRollback: (rollback: AgentRollback) => void;
	export let onCancelRollback: (rollback: AgentRollback) => void;
	export let onApplyStep: (run: AgentRun, stepId: string) => void;
	export let onPreviewArtifact: (artifactId: string) => void;

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
	$: rollbacksPageNumber = Math.floor(rollbacksPageOffset / rollbacksPageSize) + 1;
	$: rollbacksRangeStart = rollbacks.length ? rollbacksPageOffset + 1 : 0;
	$: rollbacksRangeEnd = rollbacksTotal !== null
		? Math.min(rollbacksPageOffset + rollbacks.length, rollbacksTotal)
		: rollbacksPageOffset + rollbacks.length;

	$: artifactsByRun = (runId: string) =>
		artifacts.filter((artifact) => artifact.run_id === runId);

	const statusClass = (status: string) => {
		if (status === "applied" || status === "completed") return "success";
		if (status === "failed") return "failure";
		if (status === "pending" || status === "approved") return "running";
		return "";
	};

	const artifactUrl = (artifactId: string) =>
		`${apiBase}/projects/${projectId}/agent/artifacts/${artifactId}/download`;

	const previewable = (artifact: AgentArtifact) =>
		artifact.mime_type.includes("json") ||
		artifact.mime_type.includes("text") ||
		artifact.mime_type.includes("html");
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

	{#if safeMode}
		<p class="muted">Safe mode is enabled: destructive actions require explicit approval.</p>
	{/if}

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
					<span>
						Status:
						<span class={`tag ${statusClass(run.status)}`}>{run.status}</span>
					</span>
					<span>Steps: {run.plan.steps.length}</span>
					<span>Completion: {completionPercent(run)}%</span>
					<span>Run id: {run.id}</span>
					<span>Log entries: {run.log.length}</span>
					<div class="card__actions">
						<button class="secondary" on:click={() => onReplayRun(run)}>
							Replay
						</button>
					</div>
					<details>
						<summary class="link">View run log</summary>
						<pre class="preview">{JSON.stringify(run.log, null, 2)}</pre>
					</details>
					{#if run.log.length}
						<div class="summary">
							<strong>Timeline</strong>
							<ul>
								{#each run.log as entry}
									<li>
										<strong>{entry.tool ?? "Step"}</strong>
										<span>
											Status:
											<span class={`tag ${statusClass(entry.status ?? "pending")}`}>
												{entry.status ?? "pending"}
											</span>
										</span>
										{#if entry.artifacts?.length}
											<span>Artifacts: {entry.artifacts.length}</span>
											<div class="card__actions">
												{#each entry.artifacts as artifactId}
													<button
														class="secondary"
														on:click={() => onPreviewArtifact(artifactId)}
													>
														Preview artifact
													</button>
												{/each}
											</div>
										{/if}
										{#if entry.diff}
											<details>
												<summary class="link">View diff</summary>
												<pre class="preview">{entry.diff}</pre>
											</details>
										{/if}
									</li>
							{/each}
						</ul>
					</div>
					{/if}
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
										<span>
											Status:
											<span class={`tag ${statusClass(stepStatus(run, step.id))}`}>
												{stepStatus(run, step.id)}
											</span>
										</span>
										{#if entry?.approvals?.length}
											<span>
												Approvals: {entry.approvals.length}
											</span>
										{/if}
										{#if entry?.output && typeof entry.output === 'object' && (entry.output as any).snapshot}
											{@const snapshotOutput = (entry.output as any).snapshot}
											{#if snapshotOutput?.id}
												<div class="card__actions">
													<button class="secondary" on:click={() => onRestoreSnapshot({
														id: snapshotOutput.id,
														project_id: projectId,
														kind: snapshotOutput.kind ?? 'snapshot',
														target_path: snapshotOutput.target_path ?? '',
														created_at: snapshotOutput.created_at ?? '',
														run_id: snapshotOutput.run_id ?? null,
														details: snapshotOutput.details ?? null
													})}>
														Restore
													</button>
													<button class="secondary" on:click={() => onRequestRollback({
														id: snapshotOutput.id,
														project_id: projectId,
														kind: snapshotOutput.kind ?? 'snapshot',
														target_path: snapshotOutput.target_path ?? '',
														created_at: snapshotOutput.created_at ?? '',
														run_id: snapshotOutput.run_id ?? null,
														details: snapshotOutput.details ?? null
													})}>
														Rollback
													</button>
												</div>
											{/if}
										{/if}
										{#if stepStatus(run, step.id) === "pending"}
											<div class="card__actions">
												<button
													class="secondary"
													on:click={() => onApplyStep(run, step.id ?? "")}
												>
													Approve & apply
												</button>
											</div>
										{/if}
									</li>
								{/each}
							</ul>
						</div>
					{/if}
					{#if artifactsLoading}
						<p class="muted">Loading artifacts…</p>
					{:else if artifactsError}
						<p class="error">{artifactsError}</p>
					{:else if artifactsByRun(run.id).length}
						<div class="summary">
							<strong>Artifacts</strong>
							<ul>
								{#each artifactsByRun(run.id) as artifact}
									<li>
										<strong>{artifact.type}</strong>
										<span>{artifact.mime_type}</span>
										<span>{new Date(artifact.created_at).toLocaleString()}</span>
										{#if previewable(artifact)}
											<div class="card__actions">
												<button class="secondary" on:click={() => onPreviewArtifact(artifact.id)}>
													Preview
												</button>
												<a class="link" href={artifactUrl(artifact.id)} target="_blank" rel="noreferrer">
													Download
												</a>
											</div>
										{/if}
										{#if artifact.mime_type.startsWith("image/")}
											<img class="artifact-thumb" src={artifactUrl(artifact.id)} alt={artifact.type} />
										{/if}
										{#if artifact.mime_type.includes("html")}
											<iframe class="preview__frame" src={artifactUrl(artifact.id)} title={artifact.type}></iframe>
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
						<button class="secondary" on:click={() => onRestoreSnapshot(snapshot)}>
							Restore
						</button>
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

	<div class="summary">
		<strong>Rollbacks</strong>
	</div>
	{#if rollbacksLoading}
		<p class="muted">Loading rollbacks…</p>
	{:else if rollbacksError}
		<p class="error">{rollbacksError}</p>
	{:else if rollbacks.length === 0}
		<p class="muted">No rollbacks yet.</p>
	{:else}
		<ul>
			{#each rollbacks as rollback}
				<li>
					<strong>Status: {rollback.status}</strong>
					<span>Created: {new Date(rollback.created_at).toLocaleString()}</span>
					{#if rollback.run_id}
						<span>Run: {rollback.run_id}</span>
					{/if}
					{#if rollback.snapshot_id}
						<span>Snapshot: {rollback.snapshot_id}</span>
					{/if}
					{#if rollback.note}
						<span>Note: {rollback.note}</span>
					{/if}
					{#if rollback.status === "requested"}
						<div class="card__actions">
							<button class="secondary" on:click={() => onApplyRollback(rollback)}>
								Mark applied
							</button>
							<button class="secondary" on:click={() => onCancelRollback(rollback)}>
								Cancel
							</button>
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}

	{#if !rollbacksLoading && !rollbacksError && rollbacks.length && (rollbacksHasNext || rollbacksPageOffset > 0)}
		<div class="pager">
			<span class="pager__info">
				{#if rollbacksTotal !== null}
					Showing {rollbacksRangeStart}–{rollbacksRangeEnd} of {rollbacksTotal} · Page {rollbacksPageNumber}
				{:else}
					Showing {rollbacksRangeStart}–{rollbacksRangeEnd} · Page {rollbacksPageNumber}
				{/if}
			</span>
			<div class="pager__actions">
				<button class="secondary" on:click={onPrevRollbacksPage} disabled={rollbacksPageOffset === 0}>
					Previous
				</button>
				<button class="secondary" on:click={onNextRollbacksPage} disabled={!rollbacksHasNext}>
					Next
				</button>
			</div>
		</div>
	{/if}

	{#if previewArtifactId}
		<div class="summary">
			<strong>Artifact preview</strong>
		</div>
		{#if previewLoading}
			<p class="muted">Loading preview…</p>
		{:else if previewMimeType.includes("html")}
			<iframe class="preview__frame" src={artifactUrl(previewArtifactId)} title="Artifact preview"></iframe>
		{:else}
			<pre class="preview">{previewContent}</pre>
		{/if}
	{/if}
</div>

<style>
	.artifact-thumb {
		width: 120px;
		border-radius: 6px;
		border: 1px solid #e4e4e7;
		background: #fafafa;
	}
</style>
