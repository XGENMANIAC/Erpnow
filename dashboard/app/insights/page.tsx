'use client'

import { useEffect, useState } from 'react'
import { api } from '@/lib/api'
import type { DailySummary } from '@/lib/types'

function StatCard({
  label,
  value,
  sub,
  color = 'text-gray-900',
}: {
  label: string
  value: string | number
  sub?: string
  color?: string
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <p className="text-sm text-gray-500 mb-1">{label}</p>
      <p className={`text-3xl font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  )
}

export default function InsightsPage() {
  const today = new Date().toISOString().split('T')[0]
  const [date, setDate] = useState(today)
  const [data, setData] = useState<DailySummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = async (d: string) => {
    setLoading(true)
    setError(null)
    try {
      const summary = await api.insights.daily(d)
      setData(summary)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(date) }, [date])

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 bg-white border-b border-gray-200">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Daily Insights</h1>
          <p className="text-sm text-gray-500">Sales and conversation analytics</p>
        </div>
        <input
          type="date"
          value={date}
          max={today}
          onChange={e => setDate(e.target.value)}
          className="px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none
                     focus:ring-2 focus:ring-brand-500 focus:border-transparent"
        />
      </div>

      <div className="flex-1 p-6">
        {loading && (
          <div className="text-center text-gray-400 text-sm py-16">Loading…</div>
        )}
        {error && (
          <div className="text-red-500 text-sm">{error}</div>
        )}
        {!loading && !error && data && (
          <div className="space-y-6">
            {/* Date label */}
            <p className="text-sm font-medium text-gray-500">
              {new Date(data.date + 'T00:00:00').toLocaleDateString([], {
                weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
              })}
            </p>

            {/* Conversations */}
            <section>
              <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                Conversations
              </h2>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
                <StatCard label="Total" value={data.conversations.total} />
                <StatCard
                  label="Active (bot)"
                  value={data.conversations.active}
                  color="text-green-600"
                />
                <StatCard
                  label="Human agent"
                  value={data.conversations.human}
                  color="text-yellow-600"
                />
                <StatCard
                  label="Closed"
                  value={data.conversations.closed}
                  color="text-gray-500"
                />
                <StatCard
                  label="New today"
                  value={data.conversations.new_today}
                  color="text-brand-600"
                />
              </div>
            </section>

            {/* Messages */}
            <section>
              <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                Messages today
              </h2>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                <StatCard label="Total" value={data.messages.total} />
                <StatCard label="Inbound" value={data.messages.inbound} color="text-blue-600" />
                <StatCard label="Outbound" value={data.messages.outbound} color="text-purple-600" />
              </div>
            </section>

            {/* Payments */}
            <section>
              <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">
                M-Pesa payments today
              </h2>
              <div className="grid grid-cols-2 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard label="Initiated" value={data.payments.initiated} />
                <StatCard
                  label="Completed"
                  value={data.payments.completed}
                  color="text-green-600"
                />
                <StatCard
                  label="Failed"
                  value={data.payments.failed}
                  color="text-red-500"
                />
                <StatCard
                  label="Revenue (KES)"
                  value={Number(data.payments.total_revenue_kes).toLocaleString('en-KE', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                  sub="from completed payments"
                  color="text-green-700"
                />
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  )
}
