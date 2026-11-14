"""Reminders, Calendar, Notes, Mail drafts and Shortcuts via JXA (osascript -l JavaScript).

Arguments go in as one JSON argv value, never spliced into the script, so they can't inject code.
"""
import html
import json
import subprocess
from datetime import datetime

from ..policy.paths import PolicyError
from . import ToolResult, register


def jxa(script: str, payload: dict | None = None, timeout: int = 30) -> str:
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script, json.dumps(payload or {})],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:] or "AppleScript failed")
    return r.stdout.strip()


def _iso(value, field: str) -> str:
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=None).isoformat(timespec="minutes")
    except ValueError:
        raise PolicyError(f"{field} must be a date-time like 2026-11-14T18:00")


def _when(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%a %-d %b, %-I:%M %p")


# --- Reminders ---

def _check_reminder(a: dict, c: dict) -> dict:
    title = str(a.get("title", "")).strip()
    if not title:
        raise PolicyError("reminder needs a title")
    return {"title": title, "due": _iso(a["due"], "due") if a.get("due") else None}


register(
    name="create_reminder", description="Create a macOS Reminder, optionally with a due date-time (local, ISO format).",
    params={"title": {"type": "string"}, "due": {"type": "string", "description": "e.g. 2026-11-14T18:00"}},
    required=["title"], tier=1, check=_check_reminder,
    run=lambda a, c: (jxa("""function run(argv){const a=JSON.parse(argv[0]);const R=Application('Reminders');
const p={name:a.title};if(a.due){p.dueDate=new Date(a.due);p.remindMeDate=new Date(a.due);}
R.defaultList.reminders.push(R.Reminder(p));}""", a),
        ToolResult(f"Reminder set: {a['title']}" + (f" at {_when(a['due'])}." if a["due"] else ".")))[1],
    summary=lambda a: f"Remind: {a['title']}" + (f" at {_when(a['due'])}" if a["due"] else ""),
)

register(
    name="list_reminders", description="List open (not completed) reminders.", params={}, required=[], tier=0,
    run=lambda a, c: ToolResult(jxa("""function run(){const R=Application('Reminders');const out=[];
R.lists().forEach(l=>{const rs=l.reminders.whose({completed:false});const n=rs.name(),d=rs.dueDate();
for(let i=0;i<n.length;i++)out.push(n[i]+(d[i]?' (due '+d[i].toString()+')':''));});
return out.slice(0,100).join('\\n')||'No open reminders.';}""", timeout=60)),
    untrusted_output=True, summary=lambda a: "List reminders",
)

register(
    name="complete_reminder", description="Mark an open reminder done by its exact title.",
    params={"title": {"type": "string"}}, required=["title"], tier=1,
    run=lambda a, c: ToolResult(jxa("""function run(argv){const a=JSON.parse(argv[0]);const R=Application('Reminders');
const rs=R.reminders.whose({name:a.title,completed:false});if(rs.length==0)return 'No open reminder called '+a.title+'.';
rs[0].completed=true;return 'Marked done: '+a.title+'.';}""", a)),
    summary=lambda a: f"Mark reminder done: {a['title']}",
)

# --- Calendar ---


def _check_range(a: dict, c: dict) -> dict:
    return {"start": _iso(a.get("start", ""), "start"), "end": _iso(a.get("end", ""), "end")}


register(
    name="list_events", description="List calendar events between two local date-times (ISO format).",
    params={"start": {"type": "string"}, "end": {"type": "string"}}, required=["start", "end"], tier=0,
    check=_check_range, untrusted_output=True,
    run=lambda a, c: ToolResult(jxa("""function run(argv){const a=JSON.parse(argv[0]);const C=Application('Calendar');
const s=new Date(a.start),e=new Date(a.end);const out=[];C.calendars().forEach(cal=>{
const ev=cal.events.whose({_and:[{startDate:{_greaterThan:s}},{startDate:{_lessThan:e}}]});
const t=ev.summary(),st=ev.startDate(),en=ev.endDate();
for(let i=0;i<t.length;i++)out.push([st[i],t[i]+' — '+st[i].toLocaleString()+' to '+en[i].toLocaleTimeString()]);});
out.sort((x,y)=>x[0]-y[0]);return out.map(x=>x[1]).join('\\n')||'No events.';}""", a, timeout=60)),
    summary=lambda a: f"List events {a['start']} to {a['end']}",
)


def _check_event(a: dict, c: dict) -> dict:
    title = str(a.get("title", "")).strip()
    if not title:
        raise PolicyError("event needs a title")
    r = _check_range(a, c)
    if r["end"] <= r["start"]:
        raise PolicyError("event must end after it starts")
    # calendar comes from config only; models invent names like "Primary"
    return {"title": title, **r, "calendar": c["calendar"]["default"]}


register(
    name="create_event", description="Create a calendar event / time block (local ISO date-times).",
    params={"title": {"type": "string", "description": "short event name, e.g. 'RepoRadar'"},
            "start": {"type": "string"}, "end": {"type": "string"}},
    required=["title", "start", "end"], tier=2, check=_check_event,
    run=lambda a, c: (jxa("""function run(argv){const a=JSON.parse(argv[0]);const C=Application('Calendar');
const cal=a.calendar?C.calendars.byName(a.calendar):C.calendars().find(x=>x.writable());
cal.events.push(C.Event({summary:a.title,startDate:new Date(a.start),endDate:new Date(a.end)}));}""", a),
        ToolResult(f"Added '{a['title']}' on {_when(a['start'])}."))[1],
    summary=lambda a: f"Add calendar event '{a['title']}', {_when(a['start'])} to {_when(a['end'])}",
)

# --- Notes and Mail drafts (never sent) ---

register(
    name="create_note", description="Create a note in the 'Jarvis' folder of Apple Notes (also used for drafts).",
    params={"title": {"type": "string"}, "body": {"type": "string"}}, required=["title", "body"], tier=1,
    run=lambda a, c: (jxa("""function run(argv){const a=JSON.parse(argv[0]);const N=Application('Notes');
let f=N.folders.whose({name:'Jarvis'});if(f.length==0){N.folders.push(N.Folder({name:'Jarvis'}));f=N.folders.whose({name:'Jarvis'});}
f[0].notes.push(N.Note({body:a.html}));}""",
        {"html": f"<h1>{html.escape(a['title'])}</h1>" + html.escape(a["body"]).replace("\n", "<br>")}),
        ToolResult(f"Saved note '{a['title']}' in Notes > Jarvis."))[1],
    summary=lambda a: f"Create note '{a['title']}'",
)

register(
    name="draft_email",
    description="Open a Mail draft for Naman to review and send. Jarvis never sends email.",
    params={"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
    required=["subject", "body"], tier=1,
    run=lambda a, c: (jxa("""function run(argv){const a=JSON.parse(argv[0]);const M=Application('Mail');
const m=M.OutgoingMessage({subject:a.subject,content:a.body,visible:true});M.outgoingMessages.push(m);
if(a.to)m.toRecipients.push(M.Recipient({address:a.to}));M.activate();}""", a),
        ToolResult("Draft opened in Mail. It has not been sent."))[1],
    summary=lambda a: f"Draft email '{a.get('subject', '')}' to {a.get('to') or 'nobody yet'}",
)

# --- Shortcuts (allowlist in config, each with its tier) ---


def _shortcut_tier(a: dict, c: dict) -> int:
    tier = c["shortcuts"].get(a.get("name", ""))
    if tier is None:
        raise PolicyError(f"Shortcut '{a.get('name')}' isn't in Jarvis's allowlist")
    return 2 if str(tier) in {"2", "tier2"} else 1


register(
    name="run_shortcut", description="Run one of Naman's allowlisted macOS Shortcuts by exact name.",
    params={"name": {"type": "string"}}, required=["name"], tier=_shortcut_tier,
    run=lambda a, c: (subprocess.run(["shortcuts", "run", a["name"]], check=True, timeout=120),
                      ToolResult(f"Ran Shortcut '{a['name']}'."))[1],
    summary=lambda a: f"Run Shortcut '{a['name']}'",
)
