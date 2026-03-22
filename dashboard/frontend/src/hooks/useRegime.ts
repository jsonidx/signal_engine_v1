import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

export function useRegime() {
  return useQuery({
    queryKey: ['regime', 'current'],
    queryFn: api.regimeCurrent,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  })
}
