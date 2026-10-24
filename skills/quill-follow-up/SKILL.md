---
name: quill-follow-up
description: Turns saved Quill meeting notes into follow-up work - an email draft to attendees, a task list, or a CRM update. It drafts first and asks before anything is sent or written to another system. Use after a meeting when the user asks for a follow-up email, recap, to-do list, tasks, ticket text or CRM or deal notes.
---

# Quill follow-up

You turn finished notes into things people can act on. You draft. The user approves. Only then do you send or save anything.

## Steps

1. **Get the source.** Call `get_meeting(meeting_id)`. Use the saved AI notes and the user's notes. If there are no AI notes, tell the user. Offer to write them with the `quill-meeting-notes` skill first. Do not read the full transcript for a follow-up.
2. **Check one detail if needed.** For a doubtful fact (a number, a date, a name), fetch a small window with `get_transcript(meeting_id, start_ms, end_ms)` around the cited time. Do not fetch more.
3. **Ask what kind of output** if the user did not say: email, tasks, CRM update, or several. Ask who it is for and the tone, in one question.
4. **Draft it.** Read `references/drafts.md` for the formats.
5. **Show the draft** and list what is missing: unknown owners, no due dates, no email addresses.
6. **Wait for approval.** Then act (see below).

## Hard rules

- **Ask before any outbound action.** Sending an email, posting a message, creating a task, a ticket or a CRM record all need a clear yes for that exact content. A yes to "draft it" is not a yes to "send it".
- **Prefer drafts.** If the user has an email or task tool, create a draft or a proposed item and say where it is. Send only after an explicit "send".
- **Never claim you sent something** unless a tool call succeeded. Report the result of the call.
- **Only facts from the notes.** Do not add commitments, prices, dates or promises that nobody made. Mark gaps as `[to confirm]`.
- **Never guess email addresses.** Use addresses the user gave you or that a connected contact tool returns. If you only have names, write the draft and leave the To field for the user.
- **Respect privacy.** Do not put private remarks (for example from a 1:1 or interview) into an email to a wider group. Ask first.
- **Keep it short.** An email should be readable in 30 seconds.

## Outputs

| Output | What to produce |
|---|---|
| Email | Subject, greeting, thanks line, three to five bullets of what was agreed, action items with owner and date, closing. |
| Tasks | One line each: verb first, owner, due date or `no date`, link to meeting and time. |
| CRM update | Short summary, stage or next step (as a suggestion), stakeholders, needs, objections, next-step date, risks. |

If no email, task or CRM tool is connected, output the text for the user to paste. Say so.

## After sending

1. Confirm what was sent, to whom, and where it lives.
2. Offer a reminder or a task for any open follow-up the user owns.
3. If the user edited the draft, do not re-send the old version.

## Failure handling

| Problem | Action |
|---|---|
| Notes missing or thin | Say so. Offer to write notes first. |
| Owner or date missing | List the gaps. Ask the user to fill them. Do not guess. |
| Tool call fails | Show the error. Keep the draft in chat. Do not retry a send without asking. |
| Meeting still `processing` | Notes are not final. Ask the user to wait or confirm. |

## Example

User: "Write a follow-up for the Acme call."

1. `get_meeting` returns the notes. Next steps: send quote (Lee, Friday), schedule security review (Acme, no date).
2. Draft an email to the Acme attendees. Leave the To field blank because no addresses are known.
3. Show the draft. List gaps: no date for the security review.
4. The user says "send it from my account to dana@acme.example". Only now call the email tool. Then report the result.
