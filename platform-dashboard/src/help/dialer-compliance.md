# Calling within the rules

Every number passes the compliance checks before every call. You don't need to remember them, but
you do need to know why a call didn't happen, and never work around one.

## What's checked

- **Do-not-call registers**: Australia's Do Not Call Register. A platform admin downloads the
  numbers to wash, then loads the result. A number on the register isn't called.
- **Our own do-not-call list**: permanent. Numbers are added when someone asks on a call, or by you.
  They can't be removed from the dialer.
- **What may be called** (hours and kinds of number): set per market on the server. A lawyer confirms
  it for each market before the first campaign there.
- **Line type**: mobiles are detected before each cold call. Without line-type lookups set up, no
  cold call can pass the checks.

## On every call

- The AI says it's an AI, at the start.
- If someone asks not to be called again, the AI adds them to the do-not-call list. If you're on the
  call, do it yourself straight away.
- If someone asks for a person, take over (see [live calls](live-calls.md)) or arrange a callback.

If a call was blocked and you think it shouldn't have been, ask a platform admin. Don't re-enter the
number by hand.
