<script lang="ts">
	import MaintainerGraph from '$lib/components/MaintainerGraph.svelte';
	import NodeDetailPanel from '$lib/components/NodeDetailPanel.svelte';
	import FeedstockCountChart from '$lib/components/FeedstockCountChart.svelte';
</script>

<div class="col-12">
    <h2 class="mt-5">Maintainers statistics</h2>
    <p class="text-body-secondary " style="max-width: 80ch">
        Who keeps conda-forge running: how many people maintain feedstocks, how concentrated that work is,
        and how the maintainer base has grown.
    </p>
</div>

<hr />

<div class="d-flex mt-4">
    <div class="col me-4 card text-bg-light">
        <div class="card-body">
            <span class="h3">9,100</span>
            <p class="">Maintainers</p>
            <p class="small text-secondary">Across 24.5k feedstocks</p>
        </div>
    </div>
    <div class="col me-4 card text-bg-light">
        <div class="card-body">
            <span class="h3">24,750</span>
            <p class="">Feedstocks</p>
            <p class="small text-secondary">+412 feedstocks in the last 90 days</p>
        </div>
    </div>
    <div class="col me-4 card text-bg-light">
        <div class="card-body">
            <span class="h3">2.3</span>
            <p class="">Average maintainers per feedstock</p>
            <p class="small text-secondary">Median is 2</p>
        </div>
    </div>
    <div class="col card text-bg-light">
        <div class="card-body">
            <span class="h3">8,340</span>
            <p class="">Single maintainer feedstocks</p>
            <p class=""><span class="badge text-bg-warning">35% of all feedstocks</span></p>
        </div>
    </div>
</div>

<div class="d-flex mt-5 ps-5 pe-5" style="min-height: 0;">
    <div class="col">
        <h3 class="mb-3">Feedstock growth since 2016</h3>
        <FeedstockCountChart />
    </div>
</div>
