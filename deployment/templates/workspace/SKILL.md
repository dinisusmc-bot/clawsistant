# Skill: Ask the Owner a Clarifying Question

When you are uncertain, need a preference, or require information that only the owner (Nick) can provide, you can ask a clarifying question.

## How to Ask

Send a POST request to the chat router:

```bash
curl -s -X POST http://127.0.0.1:18801/ask-owner \
  -H "Content-Type: application/json" \
  -d '{"agent": "YOUR_AGENT_NAME", "task_id": TASK_ID, "question": "Your question here"}'
```

- **agent** (required): Your agent name (e.g. "planner", "coder", "tester")
- **task_id** (optional): The numeric task ID you're working on. Include when relevant.
- **question** (required): A clear, specific question.

The question is delivered to Nick via Telegram. His answer will be appended to the task's solution field and you will be dispatched to continue.

## When to Ask

- You need a **preference** or **decision** (e.g. "Should the report include crypto prices?")
- A task is **ambiguous** and could go multiple directions
- You need **access credentials** or external info you can't look up
- You're about to make a **significant assumption** that could be wrong

## When NOT to Ask

- The answer is in USER.md, IDENTITY.md, or project context
- You can make a reasonable default choice and note it
- The question is about implementation details you can decide yourself
- You've already asked the same question recently

## Writing Good Questions

1. **Be specific** — not "What should I do?" but "Should the tech report cover AI, cybersecurity, or both?"
2. **Offer options** — "Option A: daily digest at 9am. Option B: real-time alerts. Which do you prefer?"
3. **Provide context** — briefly explain why you're asking
4. **One question per request** — don't bundle multiple questions

## Important Notes

- Questions expire after 60 minutes if unanswered
- Only ask when you're genuinely blocked — don't ask frivolous questions
- After asking, you may continue working on other tasks while waiting
- The answer arrives asynchronously; you'll be re-dispatched when it's ready
