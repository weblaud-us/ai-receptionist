"""
Background jobs on a Redis queue (Roadmap Task 3.4).

    python -m jobs            # run the jobs worker (one or more; cron jobs still run once)
    python -m jobs dead       # list jobs that failed every retry
    python -m jobs dead --clear

jobs/queue.py   enqueue() for the API
jobs/worker.py  the job functions, cron schedule and arq WorkerSettings
"""
