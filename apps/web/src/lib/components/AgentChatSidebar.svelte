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
		attachments?: ChatAttachment[];
	};

	export let messages: ChatMessage[] = [];
	export let input = '';
	export let error = '';
	export let loadingPreview = false;
	export let safeMode = true;
	export let onToggleSafeMode: () => void;
	export let onSend: () => void;
	export let onAttachDatasetPreview: () => void;
	export let onAttachRunLog: () => void;
	export let onUndo: () => void;
	export let onRedo: () => void;
</script>

<aside class="card chat-sidebar">
	<div class="card__header">
		<div>
			<h2>Agent chat</h2>
			<p>Draft instructions, attach previews, and track context.</p>
		</div>
		<button class="secondary" on:click={onToggleSafeMode}>
			Safe mode: {safeMode ? 'On' : 'Off'}
		</button>
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

	<div class="chat-messages">
		{#if messages.length === 0}
			<p class="muted">No chat messages yet.</p>
		{:else}
			{#each messages as message}
				<div class={`chat-message ${message.role}`}>
					<div class="chat-meta">
						<strong>{message.role}</strong>
						<span>{new Date(message.created_at).toLocaleTimeString()}</span>
					</div>
					<p>{message.content}</p>
					{#if message.attachments?.length}
						{#each message.attachments as attachment}
							<div class="chat-attachment">
								<strong>{attachment.title}</strong>
								<pre class="preview">{attachment.content}</pre>
							</div>
						{/each}
					{/if}
				</div>
			{/each}
		{/if}
	</div>

	<div class="chat-input">
		<textarea bind:value={input} rows="4" placeholder="Type a note or instruction"></textarea>
		<button on:click={onSend} disabled={!input.trim()}>Send</button>
	</div>
</aside>

<style>
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
		border-color: #dbeafe;
	}
	.chat-message.assistant {
		background: #ecfdf5;
		border-color: #d1fae5;
	}
	.chat-meta {
		display: flex;
		justify-content: space-between;
		font-size: 12px;
		color: #71717a;
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
	.chat-input button {
		align-self: flex-end;
	}
	.chat-attachment {
		margin-top: 8px;
	}
</style>
