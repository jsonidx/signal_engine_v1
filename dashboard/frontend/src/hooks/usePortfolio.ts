import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

export function usePortfolioSummary() {
  return useQuery({
    queryKey: ['portfolio', 'summary'],
    queryFn: api.portfolioSummary,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
  })
}

export function usePortfolioHistory(weeks: number = 52) {
  return useQuery({
    queryKey: ['portfolio', 'history', weeks],
    queryFn: () => api.portfolioHistory(weeks),
    staleTime: 15 * 60 * 1000,
  })
}

export function usePortfolioPositions() {
  return useQuery({
    queryKey: ['portfolio', 'positions'],
    queryFn: api.portfolioPositions,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
  })
}
