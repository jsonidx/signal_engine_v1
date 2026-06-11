import { QueryClient } from '@tanstack/react-query'

// PERF-007: No blanket refetchInterval — most dashboard data is static between
// pipeline/AI runs.  Refresh is driven by:
//   - explicit cache invalidation after analysis completion (useQueryClient.invalidateQueries)
//   - staleTime expiry on window refocus
//   - per-query overrides for truly live flows (analyze-job status)
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000,  // 5 min — data considered fresh after pipeline run
      refetchInterval: false,      // no passive polling by default
      retry: 1,
    },
  },
})
