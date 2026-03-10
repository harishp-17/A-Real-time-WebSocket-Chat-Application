# Chatterbox – A Real-time WebSocket Chat Application

## Overview
Chatterbox is a real-time chat application built with FastAPI and WebSockets.  
It supports:
- Multiple concurrent clients
- User registration and login
- Persistent chat history using SQLite
- A terminal-based chat client with live updates

## Tech Stack
- Backend: FastAPI, WebSockets, asyncio
- Database: SQLite
- Auth: bcrypt (hashed passwords)
- Client: Python terminal client using `websockets` + `requests`

## Project Structure
```text
chatterbox/
├── server/
│   ├── __init__.py
│   ├── main.py        # FastAPI server + WebSocket endpoints
│   └── auth.py        # SQLite + auth + persistence
├── client/
│   └── client.py      # Terminal chat client (register/login + chat)
├── database/
│   └── chatterbox.db  # Auto-created SQLite database
├── requirements.txt
└── README.md
