"""Run manual generation in the web process without a separate queue worker."""
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections, connections

# One job per web process limits the impact on the application. The database
# remains the source of truth for progress and retries across web processes.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='manual-generation')


def _run(job_id, run_token):
    close_old_connections()
    try:
        from website.tasks import generate_manual_job
        generate_manual_job(job_id, run_token)
    finally:
        connections.close_all()


def enqueue_manual(job_id, run_token):
    _executor.submit(_run, job_id, run_token)
