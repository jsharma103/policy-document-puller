# How I built this

The story, in plain language — including what didn't work.

## The problem

Log into three insurance companies, get past the password and the texted/emailed
security code, and pull back the policy PDF. The hard part is speed: under 8
seconds from submitting the code to the document on screen.

## First attempt, and why it fell short

The obvious approach: open a real browser in the background and click through each
site like a person — type the password, type the code, hit download. It worked,
but it was slow. A browser has to load the whole page and run all its code before
it can do anything. State Farm took ~17 seconds just to reach the code screen —
double the entire budget. The browser itself was the bottleneck.

## The idea that changed everything

The page you see isn't doing the real work — it's quietly talking to servers
behind the scenes. Logging in sends your password to a server and gets back a
"you're in" pass. Downloading a document is just another quiet request.

So: skip the browser and talk to those servers directly. No page to load, no
buttons — just the actual messages, which are tiny and fast.

The catch is there's no manual for those servers. I had to watch each site, work
out the exact conversation it has behind the scenes, and reproduce it myself. That
detective work was most of the project, and different for every carrier.

## Lemonade — the clean win

Worked out its whole behind-the-scenes flow. None of it needs a browser, so
Lemonade runs with no browser at all — login to document in about 3 seconds.

## Goodcover — knowing when to stop

The simplest carrier (email and password, no code). I could have done the same
trick, but its behind-the-scenes flow was a tangled, fragile mess tightly wired to
their own site. It's already fast through a browser, so I deliberately left it
there. Knowing when *not* to rebuild something was part of the work.

## State Farm — the hard one

The part I'm proudest of. Two problems: it detects anything that isn't a real
browser and quietly refuses, and its login is a multi-step back-and-forth across
several servers.

I reverse-engineered the whole flow, but kept hitting a wall on the password step
— it's guarded by a puzzle only a real browser can solve. So I compromised, and it
became the best part of the design: a browser opens *just* long enough to solve
that one puzzle, then everything else runs as fast direct messages. A browser only
where it's truly unavoidable.

Three more things I sorted out:

- **Do the slow part early.** The browser puzzle runs in the background the moment
  you pick State Farm and type your username — so the "log in" click feels instant.
- **Where requests come from.** State Farm refuses logins from cloud/data-center
  machines, which is where my app runs. So the login is routed through a mobile
  connection (like a phone on 4G); the document download doesn't care, so it goes
  direct and stays fast.
- **A safety net.** Since this is all reverse-engineered, it's fragile. If the fast
  path ever fails, the app quietly falls back to the slower-but-reliable browser
  method.

## Where it landed

All three come back well under 8 seconds, deployed on a real server. Credentials
are never saved — typed in, used once, forgotten.

## What I took away

The fastest solution and the most robust one aren't always the same, and the real
skill is picking the right one for each case — direct messages for Lemonade, a
browser for Goodcover, a careful blend for State Farm. And most of the hard work
was quiet observation before writing any code.
