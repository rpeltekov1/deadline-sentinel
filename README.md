# Deadline Sentinel

Deadline Sentinel is a read-only AI agent for graduate students who carry several courses at once. It reads Canvas assignments, Canvas announcements and university e-mail, notices when a deadline changes, including changes nobody announced, and re-plans the student's week around their lectures. Each morning it writes a short brief covering what changed, where two sources disagree, what is at risk, and what to work on today. It never submits work, edits Canvas or sends mail. It was built for the IBM SkillsBuild AI Experiential Learning Lab (US, Fall 2026) by Rostislav Peltekov, M.S. Computer Science, CSU Dominguez Hills.

## What it does

Each run goes through five steps. First, it takes a snapshot of every active course: assignment due dates from Canvas, plus the text of announcements and e-mails. Second, it pulls every date a message states and works out which item the date belongs to. It handles explicit dates such as "November 24 at 11:59 PM" and relative ones such as "next Tuesday before class". For a relative date it writes down how it read the phrase, for example that "next Tuesday", posted on Tuesday 17 November, means Tuesday 24 November, and that "before class" means the lecture start time. Third, it compares the snapshot with yesterday's and the sources with each other. A due date that changed in Canvas is reported as moved, and the report says whether anyone announced the change. A message that disagrees with Canvas is reported as a conflict: the plan uses the earlier date and asks the student to confirm with the instructor. A date found only in an e-mail becomes a new deadline, labelled with its source. Fourth, it fits each deliverable's estimated hours into free time, earliest deadline first. Free time is the working day minus lectures and a buffer around each lecture, with a daily cap, and the aim is to finish a day before each due date. Whatever cannot fit is flagged as at risk. Fifth, it writes the morning brief as text and HTML, a calendar file (week.ics) that Google Calendar or Apple Calendar can import, and plan.json for other tools.

The same logic is packaged for IBM watsonx Orchestrate. Three read-only Python tools (get_morning_brief, get_changes, get_plan_for_day) sit behind a native agent, deadline_sentinel. Its instructions require it to quote its evidence, to plan on the earlier date when sources disagree, and to refuse to invent dates or act on the student's behalf.

## Run the demo

The demo needs Python 3.10 or newer and no packages. The demo semester is the author's own: the courses, lecture times and assignment names are his. The dates and messages in demo/day1 and demo/day2 are invented for the scenario. On Monday morning Sentinel takes a baseline. By Wednesday morning four things have happened. CSC 541 Project 4 moved a day earlier in Canvas without an announcement, and the original announcement still gives the old date. A CSC 401 announcement says the Chapter 11 quiz must be done "next Tuesday before class", while Canvas still says 1 December. A CSC 581 e-mail asks for a peer-review draft by Friday at midnight, and that item is not in Canvas. CSC 500 has added a new item.

    python -m sentinel demo --pause

The two briefs print to the terminal, and the Wednesday files go to out/day2: brief.html, week.ics and plan.json. Run the tests with:

    python -m unittest discover -s tests -v

## Run it on a real Canvas account

Live mode issues only GET requests. Create a Canvas access token (Account, Settings, New access token) and export it as CANVAS_TOKEN. Copy demo/config.json, set canvas.base_url to your school's Canvas address and lectures to your timetable, and optionally set mail_dir to a folder of exported .eml messages. Then run:

    python -m sentinel live --config my-config.json

To use a watsonx.ai Granite model for date extraction in place of the built-in rules, set WATSONX_APIKEY, WATSONX_PROJECT_ID and, if needed, WATSONX_URL and WATSONX_MODEL, then add --llm. If the model call or its JSON fails, Sentinel falls back to the rules, so a model outage can never silently drop a deadline. The model path is covered by unit tests with a stubbed model; it has not been run against a live watsonx.ai project.

## Deploy the agent to watsonx Orchestrate

Install the Agent Development Kit (pip install ibm-watsonx-orchestrate) and connect it to your Orchestrate instance with orchestrate env add and orchestrate env activate. From the repository root, import the tools with the repository as the package root, then import the agent:

    orchestrate tools import -k python -f orchestrate/tools/sentinel_tools.py -r orchestrate/requirements.txt -p .
    orchestrate agents import -f orchestrate/agents/deadline_sentinel.yaml

On 18 November 2026 the three tools and the agent were imported with ADK 2.20.0 into a watsonx Orchestrate instance (Toronto region), and the agent answered questions in Orchestrate's chat using the instance's default model. In this version the Orchestrate tools run on the bundled demo semester. Connecting them to a live Canvas account through an Orchestrate connection is the next step.

## Responsible AI

Sentinel only reads, so the student stays in control of every submission and every message. Each date it derives comes with the exact sentence it came from and a note on how a relative date was read, so a wrong reading is visible and can be checked. When sources disagree it never guesses that the later date is safe: it plans on the earlier one and asks the student to confirm. Tokens stay in environment variables and are never written to disk, and the demo data contains no real messages or personal data.

## Layout

The core is in sentinel/ (sources.py, extract.py, reconcile.py, planner.py, outputs.py, pipeline.py, cli.py). The demo semester is in demo/, the unit tests are in tests/, and the watsonx Orchestrate tools and agent are in orchestrate/. The architecture diagram is docs/architecture.svg. MIT licence.
