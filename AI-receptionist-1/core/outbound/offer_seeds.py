"""
The offers the dialer starts with (seeded by migrations/m0006_sales_offers.py; edit them in the
platform dashboard afterwards).

- off_laudline        the LaudLine AI receptionist: the original scripts, unchanged in meaning
- off_weblaud_studio  WebLaud LLC's custom AI and software services, written from weblaud.com

Every claim in a seed is something weblaud.com or the product itself states. Proof points are the
only facts the agent may use to back up a claim, so add real results (client names, numbers) only
when they can be stated publicly.
"""

from __future__ import annotations

from typing import Any, Dict

from config import settings
from core.outbound import persona

LAUDLINE_ID = "off_laudline"
WEBLAUD_ID = "off_weblaud_studio"

_OPT_OUT = "If you'd rather not get calls like this, just say so and I'll take you off our list."


def weblaud_studio() -> Dict[str, Any]:
    return {
        "name": "WebLaud: custom AI and software", "brand": "weblaud", "company_name": "WebLaud",
        "persona_name": settings.SALES_PERSONA_NAME, "website": "weblaud.com",
        "what_we_sell": (
            "WebLaud is an AI-first software studio. We build custom AI automation and agents, AI voice "
            "receptionists, web apps and SaaS platforms, cross-platform mobile apps, and operations software "
            "such as client portals, ERPs and internal tools, in fixed-scope sprints."),
        "ideal_customer": (
            "Owners and managers of growing businesses that run on manual work, spreadsheets or off-the-shelf "
            "tools that don't quite fit, and founders who need a product built."),
        "value_props": [
            "Takes repetitive work off the team: data entry, lead follow-up, scheduling, reporting",
            "A fixed price agreed before any code is written, with no runaway hourly billing",
            "They own 100% of the code and the intellectual property",
            "One team for the AI and the software around it: web, mobile and integrations",
            "Ships in weeks: an AI integration in a 2 to 4 week sprint, a full platform in 8 to 14 weeks",
        ],
        "proof_points": [
            "We have built client portals, event ticketing with QR check-in, an omnichannel customer inbox, IoT "
            "farm monitoring, a restaurant point of sale and a school management system",
            "We built and run our own AI voice receptionist product, LaudLine",
            "Our engineers build real-time voice and video systems on WebRTC and LiveKit",
            "We offer a free website audit",
        ],
        "discovery_questions": [
            "What takes your team the most time each week that feels like it should be automatic?",
            "Which tools or spreadsheets are you running the business on today?",
            "Is there a project you've wanted to build but haven't had the people for?",
        ],
        "qualification_questions": [
            "Are they the person who decides on software and tools?",
            "Roughly how many people are on the team?",
            "Is this something to tackle this quarter, or later?",
            "Have they worked with a developer or an agency before?",
        ],
        "pain_hypotheses": [
            "Staff re-typing the same data into two or three systems",
            "Enquiries or leads that wait hours for a reply",
            "Software that almost fits, with the gaps patched in spreadsheets",
            "A product idea that is stuck because hiring developers is slow and expensive",
        ],
        "objections": [
            {"objection": "We already have a developer",
             "response": "Good. Many teams use us alongside their own people for the AI and automation pieces. "
                         "The discovery call shows quickly whether there's a fit, with no obligation."},
            {"objection": "AI is just hype",
             "response": "Fair. We only build what saves real hours: on the call we look at one process and say "
                         "honestly whether it's worth automating."},
            {"objection": "Too expensive / no budget",
             "response": "Every project is a fixed price agreed up front, and many start with one small "
                         "automation. The call is free and they leave with a rough idea of cost."},
            {"objection": "We're too busy right now",
             "response": "That's often the sign something should be automated. It's 15 minutes: would later "
                         "this week or next week be easier?"},
            {"objection": "We tried an agency and it went badly",
             "response": "Sorry to hear that. We agree the scope and price before any code, they own all the "
                         "code, and they see working progress every sprint."},
            {"objection": "Where are you based?",
             "response": "We're WebLaud LLC, a US software company, and we work with clients remotely."},
        ],
        "pricing": {"mode": "range",
                    "text": "Projects are fixed-price sprints, usually between four and a half and eighteen and a "
                            "half thousand US dollars, agreed before any work starts."},
        "call_goal": "book_meeting",
        "meeting": {"label": "discovery call", "length_min": 15, "service_name": settings.SALES_DEMO_SERVICE,
                    "url": settings.SALES_DEMO_MEETING_URL, "host_name": settings.SALES_FOUNDER_NAME},
        "info_email": {
            "subject": "{company}: custom AI and software for {business}",
            "body": ("Hi {first_name},\n\nThanks for taking our call. {company} is an AI-first software studio: we "
                     "build AI automation and agents, web apps and SaaS platforms, mobile apps, and operations "
                     "software such as client portals and internal tools.\n\n"
                     "Every project is a fixed-price sprint agreed before any code is written, and you own 100% of "
                     "the code. AI integrations usually ship in 2 to 4 weeks; full platforms in 8 to 14.\n\n"
                     "Reply to this email to book a free 15-minute {meeting_label}, or ask us for a free website "
                     "audit. More at {website}.")},
        "confirmation_email": {
            "subject": "Your {company} {meeting_label}: {when}",
            "body": ("Hi {first_name},\n\nYour 15-minute {meeting_label} with {host} is booked for {when}.\n"
                     "{meeting_url}\n\nTo make the most of it, have one process or project in mind that you'd like "
                     "to improve. Reply to this email if you need to change the time.")},
        "consented_opener": ("Hi, this is {persona}, an AI assistant from {company}. You asked us for a call about "
                             "custom AI and software for {business}. Is now a good time? If you'd rather not hear "
                             "from us, just say so and I'll take you off our list."),
        "voicemail": ("Hi, this is {persona}, an AI assistant from {company}, returning your request for a call "
                      "about custom AI and software. You can reach us on {callback}. If you'd prefer no more calls, "
                      "call that number and tell us. Thanks, goodbye."),
        "consent_text": (
            "By ticking this box and pressing the button, I agree that {company} may call the phone number I "
            "entered using an artificial (AI-generated) voice and automated dialling technology, to tell me about "
            "its custom AI and software services. My consent is not a condition of buying anything, and I can opt "
            "out at any time by saying so on a call."),
        "variants": [
            {"id": "ai_automation", "label": "AI automation first",
             "cold_opener": ("Hi, this is {persona}, an AI assistant calling from {company}, a software studio. Am I "
                             "speaking with the owner or manager of {business}? " + _OPT_OUT),
             "first_question": "What takes your team the most time each week that feels like it should be automatic?",
             "pitch": ("We build AI automations that take that kind of work off your team, at a fixed price agreed "
                       "up front, and you own the code. Would a free 15-minute discovery call this week be useful?"),
             "human_opener": ("Hi, this is [your name] from {company}, a software studio. Is this the owner or "
                              "manager of {business}? I'll be quick, and if you'd rather not get calls like this, "
                              "say so and I'll take you off our list.")},
            {"id": "custom_software", "label": "Website and ops platform first",
             "cold_opener": ("Hi, this is {persona}, an AI assistant calling from {company} about the software "
                             "{business} runs on. Is the owner or manager around? " + _OPT_OUT),
             "first_question": "Which tools or spreadsheets are you running the business on today?",
             "pitch": ("We build custom web apps, portals and mobile apps that replace the workarounds, in "
                       "fixed-price sprints of a few weeks. Could I set up a free 15-minute discovery call?"),
             "human_opener": ("Hi, this is [your name] from {company}. I'm calling about the software {business} "
                              "runs on. Is the owner or manager around? If you'd rather not get calls like this, "
                              "say so and I'll take you off our list.")},
        ],
    }


# Spoken when they asked us to call back (SALES_LEARNING_PLAN.md Phase 1). Like every opener: says it
# is an AI, names the company and offers the way off the list. The built-in legacy offer has none.
_CALLBACK_OPENER = ("Hi, it's {persona}, the AI assistant from {company}, calling back as arranged. Is now still a "
                    "good time? If you'd rather we didn't call, just say so and I'll take you off our list.")


def seeds() -> Dict[str, Dict[str, Any]]:
    return {LAUDLINE_ID: {**persona.legacy_offer_settings(), "callback_opener": _CALLBACK_OPENER},
            WEBLAUD_ID: {**weblaud_studio(), "callback_opener": _CALLBACK_OPENER}}
