# Note templates

Pick one. Delete empty sections. Keep headings as written so notes look the same across meetings. Replace `[t]` with a real `[h:mm:ss]` time.

Rules for every template: no invented facts, cite times, owner and due date only when stated.

## 1. General

```markdown
# <Meeting title>
<date> · <duration> · <attendees>

## Summary
Two to four sentences. What was this meeting for, and what came out of it?

## Key decisions
- <Decision> [t]

## Action items
- [ ] <Task> (owner: <name or unassigned>, due: <date or not stated>) [t]

## Discussion
- <Topic>
  - <Point> [t]

## Open questions
- <Question, and who may answer it> [t]
```

## 2. One-on-one

```markdown
# 1:1 <name> and <name>
<date> · <duration>

## Summary
## Updates
- <Person>: <update> [t]

## Feedback and concerns
- <Item> [t]

## Goals and growth
- <Item> [t]

## Decisions
## Action items
- [ ] <Task> (owner, due) [t]

## Follow up next time
```

Keep this private by default. Do not add judgments the speakers did not say.

## 3. Customer or sales call

```markdown
# <Company> call
<date> · <duration> · <their attendees> · <our attendees>

## Summary
## Their situation and needs
- <Need or pain> [t]

## What we showed or said
- <Item> [t]

## Objections and concerns
- <Concern> — <our answer, if any> [t]

## Budget, timing, decision process
- <Fact exactly as stated> [t]

## Next steps
- [ ] <Task> (owner, due) [t]

## Open questions
```

Write budget, dates and names exactly as spoken. If a number was unclear, write "unclear" and the time.

## 4. Standup

```markdown
# Standup <date>

## Per person
- **<Name>**
  - Done: <item> [t]
  - Next: <item> [t]
  - Blocked: <item or none> [t]

## Blockers needing help
- <Blocker> (who can help) [t]

## Decisions
## Action items
```

Keep it to a few lines per person.

## 5. Interview

```markdown
# Interview: <candidate or role>
<date> · <duration> · <interviewers>

## Summary
## Background
- <Fact stated by the candidate> [t]

## Questions and answers
- **Q:** <question> [t]
  - **A:** <short answer>

## Strengths seen
- <Evidence from the transcript> [t]

## Concerns or gaps
- <Evidence from the transcript> [t]

## Candidate's questions
## Next steps
```

Quote or point to evidence. Do not add a hire or no-hire verdict unless an interviewer said one. This is a person's record, so keep it factual and share it only with the user.

## 6. Brainstorm

```markdown
# Brainstorm: <topic>
<date> · <duration> · <attendees>

## Goal
## Ideas
- **<Idea>** — <who proposed it> [t]
  - Pros: <as said>
  - Cons: <as said>

## Themes
## Favorites or shortlist
- <Idea> — why [t]

## Parked ideas
## Next steps
- [ ] <Task> (owner, due) [t]
```

Capture every idea, even rejected ones, in one line. Mark which were chosen only if the group said so.

## Worked example (General, short)

```markdown
# Weekly sync
2026-10-21 · 32 min · Sam, Dana, Lee

## Summary
The team fixed the launch date and split the remaining work. Pricing is still open.

## Key decisions
- Launch on May 6. [0:18:22]

## Action items
- [ ] Send the contract (owner: Dana, due: Friday) [0:31:05]
- [ ] Review the pricing draft (owner: Lee, due: not stated) [0:27:40]

## Open questions
- Who approves the final price? [0:29:10]
```
