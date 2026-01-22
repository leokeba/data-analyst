<script lang="ts">
	type ChatAttachment = {
		type: 'dataset_preview' | 'artifact_preview' | 'diff';
		title: string;
		content: string;
		mimeType?: string;
	};

	type ChatMessage = {
		id: string;
		role: 'user' | 'assistant' | 'system';
		content: string;
		created_at: string;
		run_id?: string | null;
		attachments?: ChatAttachment[];
	};

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
			args?: Record<string, unknown> | null;
			output?: Record<string, unknown> | null;
			approvals?: { approved_by?: string; approved_at?: string; note?: string | null }[];
			created_at?: string;
		}[];
	};

	type AgentArtifact = {
		id: string;
		run_id?: string | null;
		type: string;
		path: string;
		mime_type: string;
		size: number;
		created_at: string;
	};


	export let messages: ChatMessage[] = [];
	export let input = '';
	export let error = '';
	export let loadingPreview = false;
	export let sending = false;
	export let safeMode = true;
	export let onToggleSafeMode: () => void;
	export let onSend: () => void;
	export let onAttachDatasetPreview: () => void;
	export let onAttachRunLog: () => void;
	export let onUndo: () => void;
	export let onRedo: () => void;

	export let runs: AgentRun[] = [];
	export let selectedRunId = '';
	export let followLatest = true;
	export let onSelectRun: (runId: string) => void;
	export let onToggleFollowLatest: () => void;
	export let tools: AgentTool[] = [];
	export let artifacts: AgentArtifact[] = [];
	export let onPreviewArtifact: (artifactId: string) => void;
	export let previewArtifactId = '';
	export let previewLoading = false;
	export let previewContent = '';
	export let previewMimeType = '';
	export let onClearPreview: () => void;

	$: selectedRun = runs.find((run) => run.id === selectedRunId) || null;
	$: combinedFeed = (() => {
		const feed: Array<
			| {
					kind: 'message';
					id: string;
					time: number;
					message: ChatMessage;
			  }
			| {
					kind: 'tool';
					id: string;
					time: number;
					entry: AgentRun['log'][number];
					index: number;
			  }
		> = [];
		for (const message of messages) {
			const time = message.created_at ? new Date(message.created_at).getTime() : 0;
			feed.push({ kind: 'message', id: message.id, time, message });
		}
		if (selectedRun) {
			selectedRun.log.forEach((entry, index) => {
				const time = entry.created_at ? new Date(entry.created_at).getTime() : 0;
				const id = `${selectedRun.id}-${index}`;
				feed.push({ kind: 'tool', id, time, entry, index });
			});
		}
		return feed.sort((a, b) => {
			if (a.time && b.time && a.time !== b.time) return a.time - b.time;
			if (a.time && !b.time) return -1;
			if (!a.time && b.time) return 1;
			if (a.kind === 'tool' && b.kind === 'tool') return a.index - b.index;
			return 0;
		});
	})();

	const statusClass = (status: string) => {
		if (status === 'applied' || status === 'completed') return 'success';
		if (status === 'failed') return 'failure';
		if (status === 'pending' || status === 'approved') return 'running';
		return '';
	};

	const truncateText = (text: string, limit = 800) =>
		text.length > limit ? `${text.slice(0, limit)}…` : text;

	const summarizeOutput = (output?: Record<string, unknown> | null) => {
		if (!output) return '';
		const stdout = output.stdout as string | undefined;
		const stderr = output.stderr as string | undefined;
		const path = output.path as string | undefined;
		const error = output.error as string | undefined;
		const summary: string[] = [];
		if (path) summary.push(`path: ${path}`);
		if (stdout) summary.push(`stdout: ${truncateText(stdout, 800)}`);
		if (stderr) summary.push(`stderr: ${truncateText(stderr, 1200)}`);
		if (error) summary.push(`error: ${truncateText(error, 1200)}`);
		if (!summary.length) return JSON.stringify(output, null, 2);
		return summary.join('\n');
	};

	const formatPayload = (payload: Record<string, unknown> | null | undefined) => {
		if (!payload) return '';
		try {
			return JSON.stringify(payload, null, 2);
		} catch {
			return String(payload);
		}
	};

	const resolveArtifactId = (path?: string) => {
		if (!path) return null;
		const match = artifacts.find((artifact) =>
			artifact.path === path || artifact.path.endsWith(`/${path}`)
		);
		return match?.id ?? null;
	};
</script>

<aside class="card chat-trace-panel">
	<div class="card__header">
		<div>
			<h2>Agent chat</h2>
			<p>Chat and trace agent activity in real time.</p>
		</div>
		<div class="header-actions">
			<button class="secondary" on:click={onToggleFollowLatest}>
				Follow latest: {followLatest ? 'On' : 'Off'}
			</button>
			<button class="secondary" on:click={onToggleSafeMode}>
				Safe mode: {safeMode ? 'On' : 'Off'}
			</button>
		</div>
	</div>

	<div class="summary">
		<strong>Quick actions</strong>
		<div class="card__actions">
			<button class="secondary" on:click={onAttachDatasetPreview} disabled={loadingPreview}>
				Attach dataset preview
			</button>
			<button class="secondary" on:click={onAttachRunLog} disabled={loadingPreview}>
				Attach run log
			</button>
			<button class="secondary" on:click={onUndo}>
				Undo last action
			</button>
			<button class="secondary" on:click={onRedo}>
				Redo last run
			</button>
		</div>
	</div>

	{#if error}
		<p class="error">{error}</p>
	{/if}

	<div class="summary">
		<strong>Run trace</strong>
		{#if runs.length === 0}
			<span class="muted">No runs yet.</span>
		{:else}
			<select
				value={selectedRunId}
				on:change={(event) => onSelectRun((event.target as HTMLSelectElement).value)}
			>
				{#each runs as run}
					<option value={run.id}>
						{run.plan.objective || 'Untitled plan'} ({run.id.slice(0, 8)})
					</option>
				{/each}
			</select>
		{/if}
	</div>

	{#if tools.length}
		<div class="summary">
			<strong>Available tools</strong>
			<span class="muted">{tools.length} tools</span>
		</div>
	{/if}

	<div class="chat-messages unified-feed">
		{#if combinedFeed.length === 0}
			<p class="muted">No activity yet.</p>
		{:else}
			{#each combinedFeed as item}
				{#if item.kind === 'message'}
					<div class={`chat-message ${item.message.role}`}>
						<div class="chat-meta">
							<strong>{item.message.role}</strong>
							<span>{new Date(item.message.created_at).toLocaleTimeString()}</span>
							{#if item.message.run_id}
								<button
									class="link"
									on:click={() => onSelectRun(item.message.run_id ?? '')}
								>
									Run {item.message.run_id.slice(0, 8)}
								</button>
							{/if}
						</div>
						<p>{item.message.content}</p>
						{#if item.message.attachments?.length}
							{#each item.message.attachments as attachment}
								<div class="chat-attachment">
									<strong>{attachment.title}</strong>
									<pre class="preview">{attachment.content}</pre>
								</div>
							{/each}
						{/if}
					</div>
				{:else}
					<div class="chat-message tool-entry">
						<div class="chat-meta">
							<strong>{item.entry.tool ?? 'Step'}</strong>
							<span class={`tag ${statusClass(item.entry.status ?? 'pending')}`}>
								{item.entry.status ?? 'pending'}
							</span>
							{#if item.entry.created_at}
								<span>{new Date(item.entry.created_at).toLocaleTimeString()}</span>
							{/if}
						</div>
						{#if item.entry.args}
							<div class="tool-args">
								<pre class="preview">{formatPayload(item.entry.args)}</pre>
							</div>
						{/if}
						{#if item.entry.output}
							<pre class="preview">{summarizeOutput(item.entry.output)}</pre>
						{/if}
						{#if item.entry.output?.path}
							{@const artifactId = resolveArtifactId(item.entry.output.path as string)}
							{#if artifactId}
								<div class="card__actions">
									<button class="secondary" on:click={() => onPreviewArtifact(artifactId)}>
										Preview artifact
									</button>
								</div>
								{#if previewArtifactId === artifactId}
									{#if previewLoading}
										<p class="muted">Loading preview…</p>
									{:else}
										<pre class="preview">{previewContent || 'No preview available.'}</pre>
									{/if}
								{/if}
							{/if}
						{/if}
					</div>
				{/if}
			{/each}
		{/if}
	</div>

	<div class="chat-input">
		<textarea bind:value={input} rows="4" placeholder="Type a note or instruction"></textarea>
		<button on:click={onSend} disabled={sending || !input.trim()}>
			{sending ? 'Sending…' : 'Send'}
		</button>
	</div>

	{#if previewArtifactId}
		<div class="preview-panel">
			<div class="preview-header">
				<strong>Preview</strong>
				<span class="muted">{previewMimeType || 'artifact'}</span>
				<button class="secondary" on:click={onClearPreview}>Close</button>
			</div>
			{#if previewLoading}
				<p class="muted">Loading preview…</p>
			{:else}
				<pre class="preview">{previewContent || 'No preview available.'}</pre>
			{/if}
		</div>
	{/if}
</aside>

<style>
	.chat-trace-panel {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}
	.header-actions {
		display: flex;
		gap: 8px;
	}
	.chat-messages {
		display: flex;
		flex-direction: column;
		gap: 12px;
		max-height: 60vh;
		overflow: auto;
		padding-right: 4px;
	}
	.chat-message {
		border: 1px solid #f4f4f5;
		border-radius: 8px;
		padding: 12px;
		background: #fafafa;
	}
	.chat-message.user {
		background: #eff6ff;
		border-color: #93c5fd;
	}
	.chat-message.assistant {
		background: #ecfdf5;
		border-color: #6ee7b7;
	}
	.unified-feed .tool-entry {
		background: #fdf4ff;
		border-color: #e9d5ff;
	}
	.unified-feed .chat-message.system {
		background: #fef9c3;
		border-color: #fde68a;
	}
	.chat-input {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.chat-input textarea {
		border: 1px solid #e4e4e7;
		border-radius: 6px;
		padding: 8px 12px;
		font-size: 14px;
		resize: vertical;
	}
	.preview-panel {
		border: 1px solid #e5e7eb;
		border-radius: 8px;
		padding: 12px;
		background: #fff7ed;
	}
	.preview-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 8px;
	}
	.chat-attachment {
		margin-top: 8px;
	}
	.link {
		background: none;
		border: none;
		color: #2563eb;
		cursor: pointer;
		padding: 0;
		font-size: 12px;
	}
	@media (max-width: 1100px) {
		.chat-messages {
			max-height: 45vh;
		}
	}
</style>
