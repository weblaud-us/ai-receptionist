# 🏨 AI-Powered Hotel Receptionist — Complete Setup Guide

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                       │
│  main.py  (uvicorn, lifespan, routers, middleware)          │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  WhatsApp   │  │    Voice     │  │   Admin REST +   │   │
│  │  Handlers   │  │   Twilio/   │  │   WebSocket API  │   │
│  │  (Twilio +  │  │   LiveKit)  │  │   /admin/ws      │   │
│  │   Meta)     │  │             │  │                  │   │
│  └──────┬──────┘  └──────┬──────┘  └────────┬─────────┘   │
│         │                │                  │              │
│         └────────────────┴──────────────────┘              │
│                          │                                  │
│              ┌───────────▼───────────┐                     │
│              │     AI Agent (LLM)    │                     │
│              │  OpenAI / Gemini /    │                     │
│              │  LM Studio            │                     │
│              └───────────┬───────────┘                     │
│                          │                                  │
│              ┌───────────▼───────────┐                     │
│              │   Tool Executor       │                     │
│              │  check_availability   │                     │
│              │  create_booking       │                     │
│              │  cancel_booking       │                     │
│              └───────────┬───────────┘                     │
│                          │                                  │
│              ┌───────────▼───────────┐                     │
│              │      MongoDB          │                     │
│              │  bookings / rooms /   │                     │
│              │  conversations /      │                     │
│              │  knowledge_items /    │                     │
│              │  agent_settings       │                     │
│              └───────────────────────┘                     │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Clone and enter project
cd "AI-powered hotel receptionist with calling and messaging"

py -3.12 -m venv .venv
# 2. Activate virtual environment
.venv\Scripts\activate          # Windows
.venv\Scripts\Activate.ps1
source .venv/bin/activate       # Linux/Mac

# 3. Copy and fill environment variables
cp .env.example .env
# Edit .env with your API keys

# 4. Seed room data
python -m utils.seed_rooms

# 5. Start server
uvicorn main:app --host 0.0.0.0 --port 8002 --reload

# 6. Open dashboard
http://localhost:8002/dashboard
```

## Environment Variables (Key Ones)

| Variable | Description | Required |
|---|---|---|
| `MONGODB_URI` | MongoDB Atlas or local URI | ✅ |
| `LLM_PROVIDER` | `openai` \| `gemini` | ✅ |
| `OPENAI_API_KEY` | GPT-4o for LLM + Whisper STT + TTS | If using OpenAI |
| `META_WA_ACCESS_TOKEN` | Meta WhatsApp Cloud API token | For WhatsApp |
| `META_WA_PHONE_NUMBER_ID` | Your WA phone number ID | For WhatsApp |
| `META_WA_VERIFY_TOKEN` | Webhook verify token | For WhatsApp |
| `TWILIO_ACCOUNT_SID` | Twilio credentials | For voice calls |
| `TWILIO_AUTH_TOKEN` | Twilio auth token | For voice calls |
| `LIVEKIT_URL` | LiveKit server URL | For browser calls |
| `WEBHOOK_BASE_URL` | Public URL (ngrok for local dev) | For webhooks |

## API Endpoints Reference

### Health
| Method | Path | Description |
|---|---|---|
| GET | `/` | Service info |
| GET | `/health` | Health + DB status |
| GET | `/dashboard` | Dashboard HTML |

### Admin Dashboard
| Method | Path | Description |
|---|---|---|
| GET | `/admin/dashboard` | Stats: bookings, revenue, occupancy, conversations |
| GET | `/admin/conversations` | List conversations with filter/pagination |
| GET | `/admin/conversations/{id}` | Full conversation with messages |
| POST | `/admin/conversations/{id}/handover` | Toggle AI ↔ Human handover |
| POST | `/admin/conversations/{id}/reply` | Send manual WhatsApp reply |
| POST | `/admin/conversations/{id}/mark-read` | Reset unread count |
| GET | `/admin/bookings` | List bookings with search/filter |
| GET | `/admin/bookings/{id}` | Booking detail |
| POST | `/admin/bookings/{id}/confirm` | Confirm + send WhatsApp |
| POST | `/admin/bookings/{id}/cancel` | Cancel booking |
| GET | `/admin/settings` | Agent settings |
| POST | `/admin/settings` | Save agent settings |
| GET | `/admin/knowledge` | List knowledge items |
| POST | `/admin/knowledge` | Add knowledge item |
| PUT | `/admin/knowledge/{id}` | Update knowledge item |
| DELETE | `/admin/knowledge/{id}` | Delete knowledge item |

### Admin Extras (new)
| Method | Path | Description |
|---|---|---|
| WS | `/admin/ws` | Real-time push events (new messages, bookings) |
| GET | `/admin/bookings/export-csv` | Export all bookings as CSV |
| GET | `/admin/conversations/analytics` | Daily conversation volume (last N days) |
| POST | `/admin/knowledge/test` | Test AI answer against knowledge base |
| GET | `/admin/bookings/revenue-chart` | Daily revenue data for charting |

### Bookings & Rooms
| Method | Path | Description |
|---|---|---|
| GET | `/bookings/` | List bookings |
| GET | `/bookings/{id}` | Get booking |
| POST | `/bookings/` | Create booking |
| PATCH | `/bookings/{id}/status` | Update status |
| DELETE | `/bookings/{id}` | Cancel booking |
| GET | `/bookings/{id}/calendar.ics` | Download ICS file |
| GET | `/bookings/lookup/search` | Search by phone/name/ID |
| GET | `/rooms/` | List rooms |
| POST | `/rooms/` | Create room |
| GET | `/rooms/availability` | Check availability |

### WhatsApp
| Method | Path | Description |
|---|---|---|
| POST | `/whatsapp/incoming` | Twilio WhatsApp webhook |
| GET | `/webhook` | Meta WA webhook verification |
| POST | `/webhook` | Meta WA inbound messages |

### Voice
| Method | Path | Description |
|---|---|---|
| POST | `/voice/incoming` | Twilio voice call webhook |
| GET | `/call/{room_id}` | LiveKit browser call page |
| GET | `/livekit/token` | Generate LiveKit join token |

## Dashboard Features

### 💬 Conversations Inbox
- Live WhatsApp inbox with unread badges
- Filter by All / Active / Resolved tabs
- Click any conversation to view full chat history
- **Take Over** button — disable AI and handle manually
- **Send Reply** — sends WhatsApp directly from dashboard
- Real-time updates via WebSocket (no manual refresh needed)
- Browser notifications for new messages

### 📅 Bookings Manager
- Full booking table with search + status filter
- Confirm pending bookings (triggers WhatsApp confirmation to guest)
- Cancel bookings
- Export all bookings to CSV
- Stats cards: Confirmed / Pending / Cancelled / Revenue

### 🧠 Knowledge Base
- Add/edit/delete training knowledge for the AI agent
- Coverage score and gap detection
- **AI Test Preview** — ask a question and see what the AI would answer (uses real `/admin/knowledge/test` endpoint)
- Query count tracking per item

### ⚙️ Agent Settings
- Customize greeting message (live preview on phone mockup)
- Personality: Professional / Friendly / Luxury
- Response language: English / Arabic / Hindi / Auto-detect
- Toggles: Agent Active, Auto-Booking, Reminders, Escalation Notify, Away Mode
- Business hours per day
- Escalation email
- Share Aria QR code / WhatsApp link

### 📊 Overview
- Full stats dashboard
- Revenue chart (last 14 days)
- Conversation volume chart (last 14 days)

## WebSocket Events

Connect to `ws://localhost:8002/admin/ws` for real-time events:

```json
// New inbound WhatsApp message
{ "type": "new_message", "session_id": "...", "phone": "+971...", "preview": "Hi I want to book...", "label": "Booking" }

// New booking created by AI
{ "type": "new_booking", "booking_id": "HTL1A2B3C4D", "guest_name": "Ahmed K.", "status": "confirmed", "amount": 1008 }
```

## ngrok Setup (Local Dev)

```bash
# Run ngrok
ngrok http 8002

# Copy HTTPS URL to .env
WEBHOOK_BASE_URL=https://xxxx.ngrok-free.app

# Set Meta WhatsApp webhook to:
# https://xxxx.ngrok-free.app/webhook

# Set Twilio WhatsApp webhook to:
# https://xxxx.ngrok-free.app/whatsapp/incoming
```

## Key Files

```
main.py                     ← FastAPI app entry point
config.py                   ← All settings (pydantic-settings)
ai/
  agent.py                  ← Conversation AI agent
  tools.py                  ← Hotel tools (availability, booking, cancellation)
  prompts.py                ← System prompts
  llm_provider.py           ← OpenAI / Gemini / LM Studio switch
whatsapp/
  meta_handler.py           ← Meta WhatsApp Cloud API (recommended)
  handler.py                ← Twilio WhatsApp (alternative)
voice/
  twilio_handler.py         ← Inbound voice calls
  stt_tts.py                ← Speech-to-text / text-to-speech
routers/
  admin.py                  ← Admin dashboard REST API
  admin_extras.py           ← WebSocket + CSV + analytics (new)
  api.py                    ← Public bookings/rooms API
  livekit.py                ← LiveKit room/token API
database/
  models.py                 ← Pydantic + MongoDB schemas
  mongodb.py                ← Async Motor connection
  tenancy.py                ← Hotel profile config
services/
  reminders.py              ← 24h check-in reminder scheduler
  calendar.py               ← ICS calendar file generation
ui/
  dashboard.html            ← Complete interactive dashboard
```
