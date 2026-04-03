import { Shell } from '../components/layout/Shell'
import { Top20RankingTable } from '../components/Top20RankingTable'

export function RankingsPage() {
  return (
    <Shell title="Daily Top-20">
      <Top20RankingTable />
    </Shell>
  )
}
