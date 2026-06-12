'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import type { Conversation, SSEMessage } from '@/lib/types'

const TABS = [
  { label: 'All', value: 'all' },
  { label: 'Active', value: 'active' },
  { label: 'Human', value: 'human' },
  { label: 'Closed', value: 'closed' },
]

const STATUS_BADGE: Record<string, string> = {
  active: 'bg-green-100 text-green-700',
  human:  'bg-yellow-100 text-yellow-700',
  closed: 'bg-gray-100 text-gray-500',
}

function formatTime(iso: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const diffH = (now.getTime() - d.getTime()) / 3_600_000
  if (diffH < 24) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}

export default function ConversationsPage() {
  const [tab, setTab] = useState('all')
  const [convs, setConvs] = useState<Conversation[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const convMapRef = useRef<Map<number, Conversation>>(new Map())

  const load = async (status: string) => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.conversations.list({ status, limit: 50 })
      setConvs(data.conversations)
      setTotal(data.total)
      convMapRef.current = new Map(data.conversations.map(c => [c.id, c]))
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(tab) }, [tab])

  // SSE: refresh conversation preview on new messages
  useEffect(() => {
    const es = new EventSource('/api/conversations/events')

    es.onmessage = (evt) => {
      try {
        const msg: SSEMessage = JSON.parse(evt.data)
        if (msg.type !== 'message') return
        setConvs(prev => {
          const idx = prev.findIndex(c => c.id === msg.conversation_id)
          if (idx === -1) {
            // New conversation — reload
            load(tab)
            return prev
          }
          const updated = [...prev]
          updated[idx] = {
            ...updated[idx],
            last_message: msg.body,
            last_message_at: msg.created_at,
          }
          // Bubble to top
          const [item] = updated.splice(idx, 1)
          return [item, ...updated]
        })
      } catch {
        // ignore parse errors
      }
    }

    return () => es.close()
  }, [tab])

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 bg-white border-b border-gray-200">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">Conversations</h1>
          <p className="text-sm text-gray-500">{total} total</p>
        </div>
        {/* Status tabs */}
        <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
          {TABS.map(t => (
            <button
              key={t.value}
              onClick={() => setTab(t.value)}
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === t.value
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto divide-y divide-gray-100">
        {loading && (
          <div className="flex items-center justify-center py-16 text-gray-400 text-sm">
            Loading…
          </div>
        )}
        {error && (
          <div className="px-6 py-4 text-red-500 text-sm">{error}</div>
        )}
        {!loading && !error && convs.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400">
            <svg className="w-10 h-10 mb-3 opacity-40" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
            </svg>
            <p className="text-sm">No conversations yet</p>
          </div>
        )}
        {convs.map(conv => (
          <Link
            key={conv.id}
            href={`/conversations/${conv.id}`}
            className="flex items-start gap-3 px-6 py-4 hover:bg-gray-50 transition-colors"
          >
            {/* Avatar */}
            <div className="w-10 h-10 rounded-full bg-brand-100 flex items-center justify-center flex-none">
              <span className="text-brand-700 font-semibold text-sm">
                {(conv.display_name || conv.waid).slice(0, 2).toUpperCase()}
              </span>
            </div>
            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-0.5">
                <p className="font-medium text-gray-900 text-sm truncate">
                  {conv.display_name || conv.waid}
                </p>
                <span className="text-xs text-gray-400 flex-none ml-2">
                  {formatTime(conv.last_message_at)}
                </span>
              </div>
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm text-gray-500 truncate">
                  {conv.last_message || 'No messages yet'}
                </p>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium flex-none ${STATUS_BADGE[conv.status] ?? 'bg-gray-100 text-gray-500'}`}>
                  {conv.status}
                </span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}
