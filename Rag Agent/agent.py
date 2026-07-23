"""
RAG agent for the company knowledge base.

Wraps the persisted Chroma retriever (see ingest.py / query.py) as a tool for
a Gemini-powered LlamaIndex agent, and keeps one independent conversation per
session_id so multiple users (or browser tabs) can chat concurrently without
their histories bleeding into each other.

Setup:
    pip install -r requirements.txt
    export GOOGLE_API_KEY=your-gemini-api-key   # https://aistudio.google.com/apikey

CLI usage:
    python agent.py                              # interactive chat loop
    python agent.py "does tier 2 include 3D renderings?"

The same RagAgent class backs server.py (the web UI).
"""

import asyncio
import datetime
import os
import sys
import threading

import chromadb
from dotenv import load_dotenv
from llama_index.core import VectorStoreIndex
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.tools import FunctionTool
from llama_index.core.workflow import Context
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.vector_stores.chroma import ChromaVectorStore

from ingest import CHROMA_DIR, COLLECTION_NAME, EMBED_MODEL_NAME
import google_services

load_dotenv()

if not os.environ.get("GROQ_API_KEY"):
    pass

MODEL_NAME = os.environ.get("RAG_AGENT_MODEL", "models/gemini-3.1-flash-lite")
TOP_K = 2
SESSION_IDLE_LIMIT = 200  # safety cap so a long-running server can't leak memory forever

SYSTEM_PROMPT = """You are a highly conversational, friendly, and human-like assistant for Apex Remodeling & Design.

CRITICAL RULES:
1. Every message you output MUST be very concise, small, and a maximum of 2 lines. 
2. If answering a question requires more than 2 lines of information or news, you MUST give a very brief 1-line summary and ask the user if they need the extra details or not. 
3. Only recall/refer to previous chat history if the user's current question is related to it. If they ask about something unrelated or change the topic, ignore the history and treat it as a fresh start.
4. The initial greeting is already displayed in the UI. Do NOT repeat or say "Hi, how can I help you?" again. Jump straight into responding to the user's message.
5. Be polite and conversational. Ask questions ONE BY ONE in separate turns. Never ask for multiple pieces of information in a single turn.

6. CHECK CHAT HISTORY FIRST (DO NOT RE-ASK):
   - Always read the conversation history before asking a question.
   - If the user has ALREADY provided any detail (such as project type, meeting preference, name, email, or address) earlier in the conversation, NEVER ask for it again!
   - Simply acknowledge what they provided and move directly to the NEXT missing detail.

7. INITIAL IDENTIFICATION (Support Ticket vs. Booking):
   - When a user contacts you, first identify whether their request is a Booking Request (new project/consultation) or a Support Issue/Complaint.
   - Do NOT submit a support ticket until you verify that the user previously took our services.
   - If the user reports an issue/complaint but you are not sure if they took our services, ask: "Did you previously take our remodeling services?"
   - IF THEY DID NOT TAKE OUR SERVICES: Tell them "Our team can discuss this with you to see how we can assist" and seamlessly switch to the MEETING BOOKING flow.
   - IF THEY DID TAKE OUR SERVICES: Ask them for their Order Number / Contract ID and proceed with SUPPORT TICKET CREATION.

8. SUPPORT TICKET CREATION STEPS (Strict sequential order):
   - Step 1: Identify and confirm the specific issue/complaint.
   - Step 2: Ask for their Order Number / Contract ID (if not provided).
   - Step 3: Ask for their Name.
   - Step 4: Ask for their Email address.
   - Step 5: Ask for their Address / Location.
   - Categorize the query: "Craftsmanship & Quality", "Emergency Hazard", "Billing & Invoice Dispute", "Schedule & PM Complaint", "Design Change Request", or "General Support".
   - Assign Priority (1 to 10): 1-3 Low, 4-6 Medium, 7-8 High, 9-10 Critical (active leaks, safety hazards).
   - Once all 5 details are provided, call `create_support_ticket(..., order_number=order_number)`.
   - ALWAYS output the Ticket ID in the chat right after creation! Example response: "Your ticket has been created. Ticket ID: TICK-XXXXXX. Our team will review your ticket and get back to you within 48 hours."
   - Never mention internal priority numbers or escalation flags to the user.

9. MEETING BOOKING STEPS (Strict sequential order):
   - Step 1: Ask for Project / Service Type (e.g. kitchen remodel, bathroom, room remodel).
   - Step 2: Ask for Meeting Preference (Home visit at home for quote / Office visit / Online meeting).
   - Step 3: Ask for Full Name.
   - Step 4: Ask for Email Address.
   - Step 5: Ask for Home Address / Location.
   - Step 6: Ask for preferred Date and Time (e.g. "What date and time would work best for our team to visit?").
   
   CRITICAL PROGRESSION RULE FOR BOOKING:
   - When the user provides their Address (Step 5), DO NOT ask for Project Type, Meeting Preference, Name, or Email again!
   - IMMEDIATELY proceed to Step 6 and ask for their preferred Date and Time!
   - Once Date and Time are provided (Step 6), call `check_calendar_availability` for the proposed date and time, and then call `book_meeting`.

10. TICKET STATUS & ESCALATION RULES:
    - When a user asks to check ticket status, call `get_ticket_status` and state the current status concisely. Do NOT prompt or ask the user if they want to escalate it.
    - ONLY call `escalate_ticket_to_human` if the user explicitly complains, expresses dissatisfaction, or directly asks to speak with a manager/human.

11. Do NOT use dashes (-), em-dashes (—), or double-hyphens (--) in your chat responses. Use commas, spaces, or periods instead."""



def _load_retriever():
    embed_model = GoogleGenAIEmbedding(model_name=EMBED_MODEL_NAME, api_key=os.environ.get("GEMINI_API_KEY"))
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    chroma_collection = chroma_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)
    return index.as_retriever(similarity_top_k=TOP_K)


class RagAgent:
    """Loads the retriever + LLM once, then serves many independent chat sessions.

    Session management: each session_id gets its own llama-index workflow
    Context, which holds that conversation's message history. Contexts are
    kept in memory only (no persistence across process restarts) and are
    thread-safe to create/evict via a lock.
    """

    def __init__(self):
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Put it in a "
                "'Rag Agent/.env' file."
            )

        self._retriever = _load_retriever()
        self._llm = GoogleGenAI(model=MODEL_NAME, api_key=os.environ.get("GEMINI_API_KEY"))
        self._tool = FunctionTool.from_defaults(
            fn=self._search_knowledge_base,
            name="search_knowledge_base",
            description=(
                "Search the company knowledge base (FAQs, services & pricing, "
                "general company info) for passages relevant to a question. "
                "Always call this before answering factual questions."
            ),
        )
        self._tool_current_datetime = FunctionTool.from_defaults(
            fn=google_services.get_current_datetime,
            name="get_current_datetime",
            description="Returns the real current date and time (UTC) and weekday name. Call this before interpreting any relative date (e.g. 'tomorrow', 'next week') or booking a meeting — never guess today's date."
        )
        self._tool_update_sheet = FunctionTool.from_defaults(
            fn=google_services.update_lead_sheet,
            name="update_lead_sheet",
            description="Use ONLY to save contact info if a lead shares details but does NOT book a meeting. NEVER call this tool for a booked meeting (book_meeting handles Sheets logging automatically)."
        )
        self._tool_check_availability = FunctionTool.from_defaults(
            fn=google_services.check_calendar_availability,
            name="check_calendar_availability",
            description="Checks if the user's calendar is free at the given ISO datetime string. Optional duration_minutes defaults to 30."
        )
        self._tool_book_meeting = FunctionTool.from_defaults(
            fn=google_services.book_meeting,
            name="book_meeting",
            description="Use to book a consultation meeting. It automatically checks availability, creates the event on Google Calendar, sends a confirmation email to the user, and logs the lead in the Google Sheet. Params: client_name, client_email, date_time_iso, client_address."
        )
        self._tool_cancel_meeting = FunctionTool.from_defaults(
            fn=google_services.cancel_meeting,
            name="cancel_meeting",
            description="Cancels an existing meeting for the given email. Params: client_email."
        )
        self._tool_create_ticket = FunctionTool.from_defaults(
            fn=google_services.create_support_ticket,
            name="create_support_ticket",
            description="Creates a support ticket in Google Sheets (Tickets tab), sends email confirmation, and auto-escalates if priority >= 8. Params: name, email, location, issue_description, priority, category, calendar_id, meeting_date, chat_summary."
        )
        self._tool_get_ticket_status = FunctionTool.from_defaults(
            fn=google_services.get_ticket_status,
            name="get_ticket_status",
            description="Retrieves support ticket details and resolution status by Ticket ID (e.g. TICK-123456) or client Email. Params: identifier."
        )
        self._tool_escalate_ticket = FunctionTool.from_defaults(
            fn=google_services.escalate_ticket_to_human,
            name="escalate_ticket_to_human",
            description="Escalates an existing support ticket to a human senior manager and dispatches an urgent alert email. Params: ticket_id, reason."
        )
        self._agent = FunctionAgent(
            tools=[
                self._tool,
                self._tool_current_datetime,
                self._tool_update_sheet,
                self._tool_check_availability,
                self._tool_book_meeting,
                self._tool_cancel_meeting,
                self._tool_create_ticket,
                self._tool_get_ticket_status,
                self._tool_escalate_ticket,
            ],
            llm=self._llm,
            system_prompt=SYSTEM_PROMPT,
        )
        self._sessions: dict[str, Context] = {}
        self._session_order: list[str] = []
        self._lock = threading.Lock()

    def _search_knowledge_base(self, query: str) -> str:
        """Look up relevant passages in the company knowledge base."""
        nodes = self._retriever.retrieve(query)
        if not nodes:
            return "No relevant information found in the knowledge base."
        parts = []
        for n in nodes:
            doc = n.node.metadata.get("source_doc", "unknown")
            section = n.node.metadata.get("section_title", "")
            parts.append(f"[{doc} | {section}]\n{n.node.text}")
        return "\n\n---\n\n".join(parts)

    def _get_context(self, session_id: str) -> Context:
        with self._lock:
            ctx = self._sessions.get(session_id)
            if ctx is None:
                ctx = Context(self._agent)
                self._sessions[session_id] = ctx
                self._session_order.append(session_id)
                # Evict the oldest session if we've grown past the cap.
                if len(self._session_order) > SESSION_IDLE_LIMIT:
                    oldest = self._session_order.pop(0)
                    self._sessions.pop(oldest, None)
            return ctx

    async def chat(self, session_id: str, message: str) -> str:
        """Send a message in the given session and return the agent's reply.

        Reuses that session's Context (and therefore its chat history) across
        calls, so follow-up questions like "what about tier 3?" resolve
        correctly.
        """
        ctx = self._get_context(session_id)
        now = datetime.datetime.now().astimezone()
        grounded_message = (
            f"(System note, not visible to the user: the real current date/time is "
            f"{now.strftime('%Y-%m-%d %H:%M %Z')}, {now.strftime('%A')}. Use this as ground truth "
            "for any relative-date reasoning or meeting booking.)\n"
            f"{message}"
        )
        response = await self._agent.run(user_msg=grounded_message, ctx=ctx)

        # Prune memory to minimize token usage: strip verbose React reasoning steps,
        # discard intermediate tool outputs, and clean previous user messages.
        memory = await ctx.store.get("memory")
        if memory:
            messages = await memory.aget()
            from llama_index.core.llms import TextBlock
            
            pruned_messages = []
            modified = False
            
            for msg in messages:
                role = getattr(msg.role, "value", msg.role)
                
                # Keep user messages, but strip the verbose system note prefix from past messages
                if role == "user":
                    content = msg.content or ""
                    if "(System note," in content and ")\n" in content:
                        parts = content.split(")\n", 1)
                        if len(parts) > 1:
                            msg.content = parts[1]
                            modified = True
                    pruned_messages.append(msg)
                
                # Keep assistant messages, but only keep the final clean Answer
                elif role == "assistant":
                    content = msg.content or ""
                    if not content and hasattr(msg, "blocks") and msg.blocks:
                        from llama_index.core.llms import TextBlock
                        text_blocks = [b.text for b in msg.blocks if isinstance(b, TextBlock) and b.text]
                        if text_blocks:
                            content = " ".join(text_blocks)
                            msg.content = content
                        else:
                            # Skip intermediate assistant messages with only tool calls
                            modified = True
                            continue
                    if "Answer:" in content:
                        final_answer = content.split("Answer:")[-1].strip()
                        if final_answer and final_answer != content:
                            msg.content = final_answer
                            if hasattr(msg, "blocks") and msg.blocks:
                                msg.blocks = [
                                    TextBlock(text=final_answer) if isinstance(b, TextBlock) else b
                                    for b in msg.blocks
                                ]
                            modified = True
                    pruned_messages.append(msg)
                
                # Skip tool/system messages entirely for past turns
                else:
                    modified = True
            
            # Keep up to 24 messages (12 full turns) to prevent forgetting collected details
            if len(pruned_messages) > 24:
                pruned_messages = pruned_messages[-24:]
                modified = True
            
            if modified:
                await memory.aset(pruned_messages)

        return str(response)

    def _get_async_lock(self, session_id: str) -> asyncio.Lock:
        with self._lock:
            if session_id not in self._session_async_locks:
                self._session_async_locks[session_id] = asyncio.Lock()
            return self._session_async_locks[session_id]

    async def chat_stream(self, session_id: str, message: str):
        """Send a message and stream status updates and response text chunks."""
        async_lock = self._get_async_lock(session_id)
        async with async_lock:
            ctx = self._get_context(session_id)
            now = datetime.datetime.now().astimezone()
            grounded_message = (
                f"(System note, not visible to the user: the real current date/time is "
                f"{now.strftime('%Y-%m-%d %H:%M %Z')}, {now.strftime('%A')}. Use this as ground truth "
                "for any relative-date reasoning or meeting booking.)\n"
                f"{message}"
            )

            from llama_index.core.agent.workflow import AgentStream, ToolCall
            
            handler = self._agent.run(user_msg=grounded_message, ctx=ctx)
            
            streamed_any = False
            async for event in handler.stream_events():
                if isinstance(event, ToolCall):
                    tool_name = event.tool_name
                    if tool_name in ("check_calendar_availability", "get_existing_meeting"):
                        yield {"type": "status", "text": "Let me check the calendar if our team is available at that moment..."}
                    elif tool_name == "book_meeting":
                        yield {"type": "status", "text": "I am booking a meeting for you..."}
                    elif tool_name == "cancel_meeting":
                        yield {"type": "status", "text": "I am cancelling your meeting..."}
                    elif tool_name == "create_support_ticket":
                        yield {"type": "status", "text": "I am creating your ticket and will send you details..."}
                    elif tool_name in ("get_ticket_status", "escalate_ticket_to_human"):
                        yield {"type": "status", "text": "Let me check your support ticket status..."}
                    elif tool_name == "search_knowledge_base":
                        pass
                elif isinstance(event, AgentStream):
                    clean_text = event.delta.replace("—", ", ").replace("--", ", ")
                    if clean_text:
                        streamed_any = True
                        yield {"type": "delta", "text": clean_text}

            response = await handler

            if not streamed_any:
                final_text = str(response)
                clean_text = final_text.replace("—", ", ").replace("--", ", ").strip()
                if clean_text:
                    yield {"type": "delta", "text": clean_text}

            memory = await ctx.store.get("memory")
            if memory:
                messages = await memory.aget()
                from llama_index.core.llms import TextBlock
                
                pruned_messages = []
                modified = False
                
                for msg in messages:
                    role = getattr(msg.role, "value", msg.role)
                    if role == "user":
                        content = msg.content or ""
                        if "(System note," in content and ")\n" in content:
                            parts = content.split(")\n", 1)
                            if len(parts) > 1:
                                msg.content = parts[1]
                                modified = True
                        pruned_messages.append(msg)
                    elif role == "assistant":
                        content = msg.content or ""
                        if not content and hasattr(msg, "blocks") and msg.blocks:
                            from llama_index.core.llms import TextBlock
                            text_blocks = [b.text for b in msg.blocks if isinstance(b, TextBlock) and b.text]
                            if text_blocks:
                                content = " ".join(text_blocks)
                                msg.content = content
                            else:
                                modified = True
                                continue
                        if "Answer:" in content:
                            final_answer = content.split("Answer:")[-1].strip()
                            if final_answer and final_answer != content:
                                msg.content = final_answer
                                if hasattr(msg, "blocks") and msg.blocks:
                                    msg.blocks = [
                                        TextBlock(text=final_answer) if isinstance(b, TextBlock) else b
                                        for b in msg.blocks
                                    ]
                                modified = True
                        pruned_messages.append(msg)
                    else:
                        modified = True
                
                # Keep up to 24 messages (12 full turns) to prevent forgetting collected details
                if len(pruned_messages) > 24:
                    pruned_messages = pruned_messages[-24:]
                    modified = True
                
                if modified:
                    await memory.aset(pruned_messages)

            yield {"type": "done", "session_id": session_id}


    def reset_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            if session_id in self._session_order:
                self._session_order.remove(session_id)

    def has_session(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions


def main():
    print("Initializing RAG Agent...")
    agent = RagAgent()

    print("Checking Google Services connectivity...")
    from google_services import check_google_calendar_access, check_google_sheets_access
    cal_ok, cal_msg = check_google_calendar_access()
    sheet_ok, sheet_msg = check_google_sheets_access()
    print(f"[*] Calendar status: {cal_msg}")
    print(f"[*] Sheets status: {sheet_msg}\n")

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(asyncio.run(agent.chat("cli", query)))
        return

    print("RAG agent ready (Gemini). Type a question, or 'quit' to exit.\n")
    session_id = "cli"
    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if query.lower() in ("quit", "exit"):
            break
        if not query:
            continue
        reply = asyncio.run(agent.chat(session_id, query))
        print(f"Agent: {reply}\n")


if __name__ == "__main__":
    main()