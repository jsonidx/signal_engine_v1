import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { CashAction } from '../lib/api'

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

export function useEquityScreener() {
  return useQuery({
    queryKey: ['screeners', 'equity'],
    queryFn: api.screenerEquity,
    staleTime: 10 * 60 * 1000,
    refetchInterval: 15 * 60 * 1000,
  })
}

export function useCash() {
  return useQuery({
    queryKey: ['portfolio', 'cash'],
    queryFn: api.cashGet,
    staleTime: 60 * 1000,
  })
}

export function useCashUpdate() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ action, amount }: { action: CashAction; amount: number }) =>
      api.cashUpdate(action, amount),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['portfolio', 'cash'] })
      qc.invalidateQueries({ queryKey: ['portfolio', 'summary'] })
    },
  })
}
