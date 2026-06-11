import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

// PERF-007: staleTime matches the backend TTL_SHORT (5 min).
// No refetchInterval — data refreshes on pipeline runs via cache invalidation.
export function useHeatmap() {
  return useQuery({
    queryKey: ['signals', 'heatmap'],
    queryFn: api.signalsHeatmap,
    staleTime: 5 * 60 * 1000,
  })
}

// PERF-007: 5 min staleTime — ticker data is snapshot-backed and only changes
// after an AI rerun.  staleTime: 30_000 caused a fresh API call on every mount
// which forced request-time assembly even when a fresh snapshot was available.
export function useSignalsTicker(ticker: string) {
  return useQuery({
    queryKey: ['signals', 'ticker', ticker],
    queryFn: () => api.signalsTicker(ticker),
    enabled: !!ticker,
    staleTime: 5 * 60 * 1000,
  })
}
