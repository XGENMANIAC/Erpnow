'use client'

import { useParams, useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import type { Conversation, Message } from '@/lib/types'

const STATUS_COLORS: Record<string, string> = {
  active: 'text-green-600 bg-green-50',
  human:  'text-yellow-700 bg-yellow-50',
  closed: 'text-gray-500 bg-gray-100',
}

function formatTs(iso: string | null) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function ConversationPage() {
  const { id } = useParams<{ id: string }>()
  const convId = parseInt(id, 10)
  const router = useRouter()

  const [conv, setConv] = useState<Conversation | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reply, setReply] = useState('')
  const [sending, setSending] = useState(false)
  const [takeoverLoading, setTakeoverLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const loadAll = async () => {
    try {
      const [convData, msgData] = await Promise.all([
        api.conversations.get(convId),
        api.conversations.messages(convId, { limit: 100 }),
      ])
      setConv(convData)
      setMessages(msgData.messages)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadAll() }, [convId])

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Poll for new messages every 5s
  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const msgData = await api.conversations.messages(convId, { limit: 100 })
        setMessages(msgData.messages)
        const convData = await api.conversations.get(convId)
        setConv(convData)
      } catch {
        // ignore transient errors
      }
    }, 5000)
    return () => clearInterval(t)
  }, [convId])

  const handleTakeover = async () => {
    if (!conv) return
    setTakeoverLoading(true)
    try {
      if (conv.status === 'human') {
        await api.conversations.release(convId)
        setConv({ ...conv, status: 'active' })
      } else {
        await api.conversations.takeover(convId)
        setConv({ ...conv, status: 'human' })
      }
    } catch (e) {
      alert(String(e))
    } finally {
      setTakeoverLoading(false)
    }
  }

  const handleSendReply = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!reply.trim() || !conv) return
    setSending(true)
    try {
      await api.conversations.reply(convId, reply.trim())
      setReply('')
      // Reload messages
      const msgData = await api.conversations.messages(convId, { limit: 100 })
      setMessages(msgData.messages)
    } catch (e) {
      alert(String(e))
    } finally {
      setSending(false)
    }
  }

  if (loading) {
    return <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">Loading…</div>
  }

  if (error || !conv) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3">
        <p className="text-red-500 text-sm">{error || 'Conversation not found'}</p>
        <button onClick={() => router.back()} className="text-sm text-gray-500 hover:text-gray-700">← Back</button>
      </div>
    )
  }

  const isHuman = conv.status === 'human'

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 bg-white border-b border-gray-200">
        <div className="flex items-center gap-3 min-w-0">
          <button onClick={() => router.back()} className="text-gray-400 hover:text-gray-600 flex-none">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="w-9 h-9 rounded-full bg-brand-100 flex items-center justify-center flex-none">
            <span className="text-brand-700 font-semibold text-xs">
              {(conv.display_name || conv.waid).slice(0, 2).toUpperCase()}
            </span>
          </div>
          <div className="min-w-0">
            <p className="font-semibold text-gray-900 text-sm truncate">
              {conv.display_name || conv.waid}
            </p>
            <p className="text-xs text-gray-500">{conv.phone || `+${conv.waid}`}</p>
          </div>
          <span className={`ml-2 text-xs px-2.5 py-0.5 rounded-full font-medium flex-none ${STATUS_COLORS[conv.status] ?? 'text-gray-500 bg-gray-100'}`}>
            {conv.status}
          </span>
        </div>
        <button
          onClick={handleTakeover}
          disabled={takeoverLoading || conv.status === 'closed'}
          className={`ml-4 px-4 py-2 text-sm font-medium rounded-lg transition-colors disabled:opacity-50 ${
            isHuman
              ? 'bg-green-600 hover:bg-green-700 text-white'
              : 'bg-yellow-500 hover:bg-yellow-600 text-white'
          }`}
        >
          {takeoverLoading ? '…' : isHuman ? 'Release to Bot' : 'Take Over'}
        </button>
      </div>

      {/* Human takeover banner */}
      {isHuman && (
        <div className="bg-yellow-50 border-b border-yellow-200 px-6 py-2.5 flex items-center gap-2">
          <svg className="w-4 h-4 text-yellow-600 flex-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
          </svg>
          <p className="text-yellow-700 text-sm font-medium">
            You are in control — the bot is paused for this conversation.
          </p>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
        {messages.length === 0 && (
          <p className="text-center text-gray-400 text-sm py-8">No messages yet</p>
        )}
        {messages.map(msg => (
          <div
            key={msg.id}
            className={`flex ${msg.direction === 'outbound' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-xs lg:max-w-md xl:max-w-lg rounded-2xl px-4 py-2.5 ${
                msg.direction === 'outbound'
                  ? 'bg-brand-600 text-white rounded-br-sm'
                  : 'bg-white text-gray-900 shadow-sm border border-gray-100 rounded-bl-sm'
              }`}
            >
              <p className="text-sm whitespace-pre-wrap break-words">{msg.body || '(media)'}</p>
              <p className={`text-xs mt-1 ${msg.direction === 'outbound' ? 'text-green-100' : 'text-gray-400'}`}>
                {formatTs(msg.created_at)}
              </p>
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Reply box — only in human mode */}
      {isHuman && (
        <div className="px-4 py-3 bg-white border-t border-gray-200">
          <form onSubmit={handleSendReply} className="flex gap-2">
            <input
              type="text"
              value={reply}
              onChange={e => setReply(e.target.value)}
              placeholder="Type a reply…"
              className="flex-1 px-4 py-2.5 border border-gray-200 rounded-full text-sm
                         focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
            />
            <button
              type="submit"
              disabled={sending || !reply.trim()}
              className="w-10 h-10 rounded-full bg-brand-600 hover:bg-brand-700 text-white
                         flex items-center justify-center transition-colors disabled:opacity-40"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
              </svg>
            </button>
          </form>
        </div>
      )}
    </div>
  )
}
