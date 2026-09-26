<script lang="ts">
	import {
		solveDependencyTree,
		type DependencyTreeNode as TreeNode,
		type PlatformOption
	} from '$lib/dependency-tree';
	import DependencyTreeNode from './DependencyTreeNode.svelte';

	interface Props {
		packageName: string;
		version: string | undefined;
		platform: PlatformOption | undefined;
		onResolved?: (directDependencyCount: number) => void;
	}

	let { packageName, version, platform, onResolved }: Props = $props();

	let tree = $state<TreeNode | undefined>(undefined);
	let treeLoading = $state(true);
	let treeError = $state<string | undefined>(undefined);
	let controlSignal = $state<{ action: 'expand' | 'collapse' } | undefined>(undefined);

	function expandAll() {
		controlSignal = { action: 'expand' };
	}

	function collapseAll() {
		controlSignal = { action: 'collapse' };
	}

	$effect(() => {
		const name = packageName;
		const selectedVersion = version;
		const selectedPlatform = platform;

		if (!selectedVersion || !selectedPlatform) {
			tree = undefined;
			return;
		}

		tree = undefined;
		treeError = undefined;
		treeLoading = true;
		controlSignal = undefined;

		solveDependencyTree(name, selectedVersion, selectedPlatform)
			.then((result) => {
				if (name !== packageName || selectedVersion !== version || selectedPlatform !== platform)
					return;
				tree = result.root;
				onResolved?.(result.directDependencyCount);
			})
			.catch((e) => {
				if (name !== packageName || selectedVersion !== version || selectedPlatform !== platform)
					return;
				treeError = e instanceof Error ? e.message : String(e);
			})
			.finally(() => {
				if (name !== packageName || selectedVersion !== version || selectedPlatform !== platform)
					return;
				treeLoading = false;
			});
	});
</script>

<div class="d-flex align-items-center justify-content-between mb-3">
	<h3 class="h6 mb-0">Dependency tree</h3>
	{#if tree && tree.children.length > 0}
		<div class="d-flex gap-2">
			<button type="button" class="btn btn-outline-secondary btn-sm" onclick={expandAll}>
				Expand all
			</button>
			<button type="button" class="btn btn-outline-secondary btn-sm" onclick={collapseAll}>
				Collapse all
			</button>
		</div>
	{/if}
</div>

<div class="dependency-tree-scroll">
	{#if treeError}
		<p class="error small mb-0 solver-error">Could not solve dependencies: {treeError}</p>
	{:else if treeLoading}
		<div class="loading-indicator">
			<div class="spinner-border text-secondary" role="status">
				<span class="visually-hidden">Loading…</span>
			</div>
			<p class="text-body-secondary mt-2 mb-0">Loading…</p>
		</div>
	{:else if tree}
		{#if tree.children.length === 0}
			<p class="text-body-secondary small mb-0">No known direct dependencies.</p>
		{:else}
			<DependencyTreeNode node={tree} {controlSignal} />
		{/if}
	{/if}
</div>

<style>
	.error {
		color: var(--bs-danger);
	}

	.solver-error {
		white-space: pre-wrap;
	}

	.dependency-tree-scroll {
		min-height: 300px;
		max-height: 600px;
		overflow-y: auto;
	}

	.loading-indicator {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		height: 100%;
		min-height: 250px;
	}

	.loading-indicator .spinner-border {
		width: 3.5rem;
		height: 3.5rem;
		border-width: 0.4em;
	}

	.loading-indicator p {
		font-size: 1.1rem;
	}
</style>
