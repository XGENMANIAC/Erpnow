export interface Conversation {
  id: number
  channel: string
  waid: string
  display_name: string | null
  phone: string | null
  erp_customer_name: string | null
  status: 'active' | 'human' | 'closed'
  last_message: string | null
  last_message_at: string | null
  created_at: string | null
}

export interface ConversationListResponse {
  conversations: Conversation[]
  total: number
  limit: number
  offset: number
}

export interface Message {
  id: number
  message_id: string
  direction: 'inbound' | 'outbound'
  message_type: string
  body: string | null
  media_id: string | null
  created_at: string | null
}

export interface MessageListResponse {
  messages: Message[]
  total: number
  limit: number
}

export interface DailySummary {
  date: string
  conversations: {
    total: number
    active: number
    human: number
    closed: number
    new_today: number
  }
  messages: {
    total: number
    inbound: number
    outbound: number
  }
  payments: {
    initiated: number
    completed: number
    failed: number
    total_revenue_kes: string
  }
}

export interface SSEMessage {
  type: 'message'
  conversation_id: number
  message_id: number
  direction: 'inbound' | 'outbound'
  body: string | null
  waid: string
  display_name: string | null
  created_at: string | null
}
