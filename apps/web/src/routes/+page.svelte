<svelte:head>
	<title>Data Analyst Control Plane</title>
</svelte:head>

<script lang="ts">
	import { onMount } from "svelte";

	type Project = {
		id: string;
		name: string;
		created_at: string;
		workspace_path: string;
	};

	let projects: Project[] = [];
	let loading = true;
	let error = "";

	const apiBase = "http://localhost:8000";

	onMount(async () => {
		try {
			const response = await fetch(`${apiBase}/projects`);
			if (!response.ok) {
				throw new Error(`API error: ${response.status}`);
			}
			projects = await response.json();
		} catch (err) {
			error = err instanceof Error ? err.message : "Failed to load projects";
		} finally {
			loading = false;
		}
	});
</script>

<main class="page">
	<section class="hero">
		<p class="eyebrow">data-analyst</p>
		<h1>Control plane</h1>
		<p class="lede">
			Manage projects, datasets, runs, and reports from a single place.
		</p>
	</section>

	<section class="grid">
		<div class="card">
			<h2>Projects</h2>
			<p>Create isolated workspaces and environments.</p>
			{#if loading}
				<p class="muted">Loading projects…</p>
			{:else if error}
				<p class="error">{error}</p>
			{:else if projects.length === 0}
				<p class="muted">No projects yet.</p>
			{:else}
				<ul>
					{#each projects as project}
						<li>
							<strong>{project.name}</strong>
							<span>{project.workspace_path}</span>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
		<div class="card">
			<h2>Datasets</h2>
			<p>Track ingestion sources, profiling, and quality checks.</p>
		</div>
		<div class="card">
			<h2>Runs</h2>
			<p>Monitor profiling, analysis, and report runs.</p>
		</div>
		<div class="card">
			<h2>Reports</h2>
			<p>Publish markdown, HTML, and PDF outputs.</p>
		</div>
	</section>
</main>

<style>
	:global(body) {
		margin: 0;
		font-family: "Inter", system-ui, sans-serif;
		color: #0f172a;
		background: #f8fafc;
	}

	.page {
		min-height: 100vh;
		padding: 64px 8vw;
		display: grid;
		gap: 48px;
	}

	.hero {
		display: grid;
		gap: 16px;
	}

	.eyebrow {
		text-transform: uppercase;
		letter-spacing: 0.14em;
		font-size: 12px;
		color: #64748b;
	}

	.lede {
		font-size: 18px;
		color: #475569;
		max-width: 640px;
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
		gap: 20px;
	}

	.card {
		background: white;
		border-radius: 16px;
		padding: 20px;
		box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
		border: 1px solid #e2e8f0;
	}

	.card h2 {
		margin: 0 0 8px;
		font-size: 18px;
	}

	.card p {
		margin: 0;
		color: #64748b;
	}

	.card ul {
		list-style: none;
		padding: 0;
		margin: 12px 0 0;
		display: grid;
		gap: 8px;
	}

	.card li {
		display: flex;
		flex-direction: column;
		gap: 4px;
		padding: 10px 12px;
		border-radius: 12px;
		background: #f1f5f9;
		font-size: 13px;
	}

	.card li span {
		color: #64748b;
		word-break: break-all;
	}

	.muted {
		margin-top: 12px;
		color: #94a3b8;
		font-size: 13px;
	}

	.error {
		margin-top: 12px;
		color: #dc2626;
		font-size: 13px;
	}
</style>
