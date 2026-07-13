# Prompt to Create a Function Agent with RAG, Google Sheets, Google Calendar, and Email Integration

This document contains a comprehensive, production-grade prompt designed to guide a code-generation LLM (like Gemini or ChatGPT) in building the `agent.py` script. It combines the information retrieval pipeline from the RAG agent with Google Sheets logging, Google Calendar booking, and SMTP email confirmations.

***

## How to Use This Prompt
Copy and paste the section below into a code-generation LLM to generate your complete `agent.py` codebase.

---

### [START OF CODE GENERATION PROMPT]

You are an expert AI software engineer specialized in Python, AI agents, and Google API integrations. Your task is to write a single production-grade Python script, `agent.py`, that implements a LlamaIndex-based `FunctionAgent` (or equivalent LangChain agent) acting as a smart, conversational assistant for a business.

The agent needs to interact with users over a chat interface, answer their questions using a local retrieval-augmented generation (RAG) system, log lead details to a Google Sheet, and manage future-only appointments on a Google Calendar with email notifications.

Here is the exact structure, integrations, and logic required:

---

### 1. Existing Project Reference & Environment Context
The workspace already contains the following assets:
- **RAG Pipeline**:
  - `chroma_db/`: A persisted vector store containing embedded document chunks.
  - `ingest.py`: Chunking and embedding code.
  - `query.py`: Retrieves contexts using `chromadb.PersistentClient` and `HuggingFaceEmbedding` (model: `BAAI/bge-small-en-v1.5`).
- **Google Sheets / API Utilities**:
  - `Client_Secret.json`: Google Cloud credentials for Sheets and Calendar.
  - `token_sheets_v4.pickle`: Cached sheets OAuth token.
  - `Googlesheet storing/Google.py`: Helper script containing `Create_Service` for Google APIs.
  - `Lead capture/main.py`: Environment loader for SMTP variables:
    - `SPREADSHEET_ID`
    - `SMTP_HOST` (default: `smtp.gmail.com`)
    - `SMTP_PORT` (default: `587`)
    - `SMTP_USER`
    - `SMTP_PASSWORD`
    - `ADMIN_EMAIL`

---

### 2. Required Function Agent Tools
The agent must be wired with the following python functions wrapped as agent tools:

#### Tool 1: `search_knowledge_base(query: str) -> str`
- **Purpose**: Search the local Chroma database for answers to customer questions.
- **Implementation**:
  - Load the embedding model `HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")`.
  - Connect to the Chroma client `chromadb.PersistentClient(path="chroma_db")`.
  - Fetch the collection, initialize `ChromaVectorStore`, and build a `VectorStoreIndex`.
  - Create a retriever with `similarity_top_k=3` and return the concatenated text from retrieved nodes.

#### Tool 2: `log_lead_to_sheet(name: str, email: str, business_needs: str, package: str) -> str`
- **Purpose**: Log lead details, preferences, and recommended packages into Google Sheets as soon as they are gathered.
- **Implementation**:
  - Initialize the Google Sheets service using the existing `Client_Secret.json` credentials and the scope `https://www.googleapis.com/auth/spreadsheets`.
  - Compute the next ID (either auto-increment from Column A or fall back to a timestamp if empty).
  - Append the values: `[ID, Name, Email, Business Needs, Package Recommended, Timestamp]` to the spreadsheet.
  - Return a status message indicating success or fallback mock-logging output.

#### Tool 3: `check_calendar_availability(start_time_iso: str) -> bool`
- **Purpose**: Check if a requested time slot is available on the Google Calendar.
- **Implementation**:
  - Query Google Calendar API (`events().list()`) for events overlapping with the requested time (assume a 30-minute or 1-hour slot duration).
  - Return `True` if free, `False` if busy.

#### Tool 4: `book_meeting(name: str, email: str, start_time_iso: str) -> str`
- **Purpose**: Schedule an appointment on Google Calendar and send email confirmations.
- **Requirements & Validations**:
  - **Future-Only Check**: Parse the `start_time_iso`. If it represents a past date or time, reject it immediately with an error message.
  - **Spam Prevention**: 
    - Validate that the email address format is correct using a robust regex.
    - Check session memory to ensure the user is not making rapid, repetitive duplicate requests or showing bot-like activity.
  - **Google Calendar API Integration**:
    - Call `events().insert()` on the Google Calendar API.
    - Set the summary to `"Consultation with " + name`, and invite the user as an attendee.
  - **Email Notification**:
    - Automatically send a calendar invite/confirmation email to both the admin (`ADMIN_EMAIL`) and the user (`email`) using `smtplib` (with SSL/TLS and `SMTP_PASSWORD`).
    - Run an internal error check verifying that the email was successfully accepted by the SMTP server for both recipients.
  - **Return Value**: A descriptive status message (e.g., `"Meeting booked successfully at [time] and invites sent to [emails]"`).

#### Tool 5: `cancel_or_modify_meeting(email: str, old_time_iso: str, action: str, new_time_iso: str = None) -> str`
- **Purpose**: Allow users to cancel or reschedule their newly booked meeting in the same chat session.
- **Implementation**:
  - Search for the existing event matching the attendee's email and the scheduled time.
  - If `action` is `"cancel"`, delete the calendar event and send a cancellation email.
  - If `action` is `"modify"`, update the event start/end times to `new_time_iso` (after verifying that the new slot is free and in the future) and send updated invites.

---

### 3. Agent System Prompt (Behavioral Instructions)
When initializing the agent, supply the following system instructions to the LLM:

```markdown
# Role & Persona
You are a highly intelligent, human-like, conversational, and non-pushy business assistant. Your goal is to guide visitors through their questions about company services, recommend suitable packages, capture their lead details, and help them schedule a meeting if they are interested.

# Behavioral & Conversational Guidelines
1. **Organic Information Gathering**: 
   - Never present the user with a rigid form or ask for a list of details (e.g., name, email, needs) in a single message.
   - Gather lead details naturally as part of an organic, flowing conversation. 
   - Start by addressing their initial query. Then, casually ask for details (e.g., "By the way, what kind of project are you working on?", "Could I get your name so I know who I'm talking to?", "What's the best email to send this info to?").
   
2. **Dynamic Package Recommendation**:
   - As the user explains their business needs, process them and dynamically suggest the most relevant package from the business offerings (retrieve package details using `search_knowledge_base` first).
   
3. **Strict Anti-Looping Protocol**:
   - Pay close attention to the history. If the user repeats an objection, repeats a question, or seems confused, **do not** repeat your previous response.
   - Pivot the conversation: try a different explanation, address their concern from a new perspective, offer a direct solution, or suggest booking a meeting to speak with a human expert.

4. **Integration Workflow**:
   - **Log Leads Immediately**: Once you have collected the user's name, email, business needs, and recommended a package, call `log_lead_to_sheet` immediately in the background. Do not wait for them to book a meeting.
   - **Calendar Availability**: Before booking a meeting, ask the user for their preferred date and time. Check availability using `check_calendar_availability` before booking.
   - **Confirmation**: Confirm the slot with the user before calling `book_meeting`.
   - **Cancellations/Modifications**: If the user changes their mind or requests a different time within the same chat session, call `cancel_or_modify_meeting` to handle it.
```

---

### 4. Implementation Structure in Python
Please output a clean, well-documented `agent.py` file. Keep the following code organization:
- Use `.env` parsing to extract all configuration secrets.
- Use LlamaIndex `FunctionAgent` (from `llama_index.core.agent` or `llama_index.llms.openai` / `llama_index.llms.gemini` depending on available keys; default to supporting OpenAI or Gemini LLM integrations).
- Include comprehensive error handling, logging, and input validation inside each tool function.
- Write a simple command-line chat loop at the bottom of the script for easy local testing:
  ```python
  if __name__ == "__main__":
      # Run simple chat loop
  ```

### [END OF CODE GENERATION PROMPT]
