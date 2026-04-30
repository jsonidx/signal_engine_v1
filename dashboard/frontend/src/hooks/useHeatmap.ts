import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

export function useHeatmap() {
  return useQuery({
    queryKey: ['signals', 'heatmap'],
    queryFn: api.signalsHeatmap,
    staleTime: 10 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
  })
}

export function useSignalsTicker(ticker: string) {
  return useQuery({
    queryKey: ['signals', 'ticker', ticker],
    queryFn: () => api.signalsTicker(ticker),
    enabled: !!ticker,
    staleTime: 0,
  })
}
