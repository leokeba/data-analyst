<script lang="ts">
	type AgentSkill = {
		id: string;
		project_id: string;
		name: string;
		description: string;
		prompt_template?: string | null;
		toolchain?: string[] | null;
		enabled: boolean;
		created_at: string;
		updated_at: string;
	};

	export let skills: AgentSkill[] = [];
	export let loading = false;
	export let error = "";
	export let actionError = "";
	export let name = "";
	export let description = "";
	export let promptTemplate = "";
	export let toolchain = "";
	export let isCreating = false;
	export let onCreate: () => void;
	export let onToggle: (skill: AgentSkill) => void;
	export let onRun: (skill: AgentSkill) => void;
	export let onEdit: (skill: AgentSkill) => void;
	export let onDelete: (skill: AgentSkill) => void;
</script>

<div class="card">
	<div class="card__header">
		<div>
			<h2>Skills</h2>
			<p>Manage user-defined skills for agent plans.</p>
		</div>
	</div>
	<div class="form">
		<input placeholder="Skill name" bind:value={name} disabled={isCreating} />
		<input placeholder="Description" bind:value={description} disabled={isCreating} />
		<input placeholder="Prompt template" bind:value={promptTemplate} disabled={isCreating} />
		<input
			placeholder="Toolchain (comma-separated)"
			bind:value={toolchain}
			disabled={isCreating}
		/>
		<button on:click={onCreate} disabled={isCreating}>
			{isCreating ? "Creating…" : "Add skill"}
		</button>
	</div>
	{#if actionError}
		<p class="error">{actionError}</p>
	{/if}
	{#if loading}
		<p class="muted">Loading skills…</p>
	{:else if error}
		<p class="error">{error}</p>
	{:else if skills.length === 0}
		<p class="muted">No skills yet.</p>
	{:else}
		<ul>
			{#each skills as skill}
				<li>
					<strong>{skill.name}</strong>
					<span>{skill.description}</span>
					{#if skill.toolchain?.length}
						<span>Toolchain: {skill.toolchain.join(", ")}</span>
					{/if}
					{#if skill.prompt_template}
						<span>Prompt: {skill.prompt_template}</span>
					{/if}
					<span>Enabled: {skill.enabled ? "Yes" : "No"}</span>
					<div class="card__actions">
						<button class="secondary" on:click={() => onRun(skill)}>
							Run
						</button>
						<button class="secondary" on:click={() => onEdit(skill)}>
							Edit
						</button>
						<button class="secondary" on:click={() => onToggle(skill)}>
							{skill.enabled ? "Disable" : "Enable"}
						</button>
						<button class="danger" on:click={() => onDelete(skill)}>
							Delete
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>
