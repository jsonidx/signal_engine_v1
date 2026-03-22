import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

export function useDarkPool() {
  return useQuery({
    queryKey: ['darkpool', 'latest'],
    queryFn: api.darkpoolLatest,
    staleTime: 15 * 60 * 1000,
    refetchInterval: 30 * 60 * 1000,
  })
}

export function useDarkPoolTicker(ticker: string) {
  return useQuery({
    queryKey: ['darkpool', 'ticker', ticker],
    queryFn: () => api.darkpoolTicker(ticker),
    enabled: !!ticker,
    staleTime: 15 * 60 * 1000,
  })
}
