import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { Check, RotateCcw, Save } from 'lucide-react'
import { Shell } from '../components/layout/Shell'
import { LoadingSkeleton } from '../components/ui/LoadingSkeleton'
import { api, SettingItem } from '../lib/api'

const GROUP_DESCRIPTIONS: Record<string, string> = {
  'AI Analysis':  'Controls which LLM runs ai_quant and minimum thresholds for analysis',
  'Calibration':  'How historical accuracy bias is applied to adjust AI-generated targets',
  'Portfolio':    'Position sizing, Kelly fraction, and allocation limits',
  'Universe':     'Filters applied when building the daily ticker universe',
  'Alerts':       'Telegram notification credentials',
}

const GROUP_ORDER = ['AI Analysis', 'Calibration', 'Portfolio', 'Universe', 'Alerts']

// ─── Individual setting row ───────────────────────────────────────────────────

function SettingRow({ item }: { item: SettingItem }) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState(item.value)
  const [saved, setSaved] = useState(false)
  const dirty = draft !== item.value

  const mutation = useMutation({
    mutationFn: () => api.updateSetting(item.key, draft),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    },
  })

  const handleReset = () => setDraft(item.default)

  return (
    <div className={clsx(
      'grid grid-cols-[1fr_auto] gap-4 items-start py-3 border-b border-border-subtle/50 last:border-0',
      dirty && 'bg-accent-amber/3'
    )}>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-semibold text-text-primary">{item.label}</span>
          {draft !== item.default && (
            <span className="font-mono text-[9px] px-1 py-0.5 rounded bg-accent-amber/15 text-accent-amber border border-accent-amber/30">
              modified
            </span>
          )}
        </div>
        <p className="font-mono text-[10px] text-text-tertiary mt-0.5">{item.description}</p>
        <p className="font-mono text-[9px] text-text-tertiary/50 mt-0.5">default: {item.default || '—'}</p>
      </div>

      <div className="flex items-center gap-2 pt-0.5">
        {/* Input */}
        {item.type === 'select' && item.options ? (
          <select
            value={draft}
            onChange={e => setDraft(e.target.value)}
            className="font-mono text-xs px-2 py-1.5 rounded border border-border-subtle bg-bg-elevated text-text-primary focus:outline-none focus:border-accent-blue min-w-[220px]"
          >
            {item.options.map(o => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        ) : item.type === 'secret' ? (
          <input
            type="password"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            placeholder="••••••••"
            className="font-mono text-xs px-2 py-1.5 rounded border border-border-subtle bg-bg-elevated text-text-primary focus:outline-none focus:border-accent-blue w-48"
          />
        ) : (
          <input
            type={item.type === 'number' ? 'number' : 'text'}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            step={item.type === 'number' ? 'any' : undefined}
            className="font-mono text-xs px-2 py-1.5 rounded border border-border-subtle bg-bg-elevated text-text-primary focus:outline-none focus:border-accent-blue w-36 text-right"
          />
        )}

        {/* Reset to default */}
        <button
          onClick={handleReset}
          disabled={draft === item.default}
          title="Reset to default"
          className="p-1.5 rounded border border-border-subtle text-text-tertiary hover:text-text-secondary disabled:opacity-30 transition-colors"
        >
          <RotateCcw size={11} />
        </button>

        {/* Save */}
        <button
          onClick={() => mutation.mutate()}
          disabled={!dirty || mutation.isPending}
          className={clsx(
            'flex items-center gap-1 font-mono text-[10px] px-2 py-1.5 rounded border transition-colors',
            saved
              ? 'bg-accent-green/15 border-accent-green/30 text-accent-green'
              : dirty
                ? 'bg-accent-blue/15 border-accent-blue/30 text-accent-blue hover:bg-accent-blue/25'
                : 'border-border-subtle text-text-tertiary opacity-40'
          )}
        >
          {saved ? <Check size={11} /> : <Save size={11} />}
          {saved ? 'Saved' : 'Save'}
        </button>
      </div>
    </div>
  )
}

// ─── Group card ───────────────────────────────────────────────────────────────

function SettingGroup({ name, items }: { name: string; items: SettingItem[] }) {
  return (
    <div className="bg-bg-surface border border-border-subtle rounded overflow-hidden">
      <div className="px-4 py-3 border-b border-border-subtle bg-bg-elevated/30">
        <h2 className="font-mono text-xs font-semibold text-text-primary">{name}</h2>
        {GROUP_DESCRIPTIONS[name] && (
          <p className="font-mono text-[10px] text-text-tertiary mt-0.5">{GROUP_DESCRIPTIONS[name]}</p>
        )}
      </div>
      <div className="px-4">
        {items.map(item => <SettingRow key={item.key} item={item} />)}
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function SettingsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
    staleTime: 0,
  })

  const groups = data?.groups ?? {}
  const orderedGroups = [
    ...GROUP_ORDER.filter(g => groups[g]),
    ...Object.keys(groups).filter(g => !GROUP_ORDER.includes(g)),
  ]

  return (
    <Shell title="Settings">
      <div className="max-w-3xl mx-auto space-y-5">
        <div>
          <h1 className="font-mono text-sm font-semibold text-text-primary">Settings</h1>
          <p className="font-mono text-[10px] text-text-tertiary mt-0.5">
            Changes are saved to the database and take effect on the next pipeline run or page refresh.
            LLM changes apply immediately to manual re-runs from the ticker deep dive.
          </p>
        </div>

        {isLoading ? (
          <LoadingSkeleton rows={8} />
        ) : (
          orderedGroups.map(name => (
            <SettingGroup key={name} name={name} items={groups[name]} />
          ))
        )}
      </div>
    </Shell>
  )
}
