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

<div class="dependency-tree-scroll">
	{#if treeError}
		<p class="error small mb-0">Could not solve dependencies: {treeError}</p>
	{:else if treeLoading}
		<p class="text-body-secondary small mb-0">Solving dependency tree…</p>
	{:else if tree}
		{#if tree.children.length === 0}
			<p class="text-body-secondary small mb-0">No known direct dependencies.</p>
		{:else}
			<DependencyTreeNode node={tree} />
		{/if}
	{/if}
</div>

<style>
	.error {
		color: var(--bs-danger);
	}

	.dependency-tree-scroll {
		min-height: 300px;
		max-height: 300px;
		overflow-y: auto;
	}
</style>
