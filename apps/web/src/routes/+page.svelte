<svelte:head>
	<title>Data Analyst Control Plane</title>
</svelte:head>

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
</style>
