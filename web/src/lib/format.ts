export function formatNumber(value: number): string {
	return value.toLocaleString();
}

export function formatPercent(value: number): string {
	return `${value.toLocaleString()}%`;
}
